import json
import os
import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

# Initialize clients
dynamodb = boto3.resource('dynamodb')
# Regional endpoint for presigned URLs (bucket.s3.<region>.amazonaws.com).
s3_client = boto3.client('s3', config=Config(signature_version='s3v4', s3={'addressing_style': 'virtual'}))

TABLE_NAME = os.environ.get('TABLE_NAME')
BUCKET_NAME = os.environ.get('BUCKET_NAME')
table = dynamodb.Table(TABLE_NAME)

def handler(event, context):

    # Check if Lex is calling this Lambda
    if 'sessionState' in event:
        return handle_lex_request(event)
    
    # 1. Parse the API Gateway event body
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid JSON body'})
        }

    # The frontend will send a dictionary of tags and counts, e.g., {"fox": 2}
    search_tags = body.get('tags', {})

    if not search_tags:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing tags for search'})
        }

    # 2. Query the GSI for each tag and find the intersection (Logical AND)
    matching_file_ids = None

    for tag, min_count in search_tags.items():
        # Query the tag-count-index GSI
        response = table.query(
            IndexName='tag-count-index',
            KeyConditionExpression=Key('tag').eq(tag) & Key('tag_count').gte(min_count)
        )
        
        # Extract file_ids from the query results into a Python Set
        current_file_ids = set()
        for item in response.get('Items', []):
            current_file_ids.add(item['file_id'])
            
        # Intersection logic:
        if matching_file_ids is None:
            matching_file_ids = current_file_ids
        else:
            matching_file_ids = matching_file_ids.intersection(current_file_ids)

    # If no files matched the logical AND, return an empty list
    if not matching_file_ids:
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'results': []})
        }

    # 3. Fetch META items and generate Presigned URLs for the frontend
    results = []
    
    for file_id in matching_file_ids:
        # Fetch the META item for this file
        meta_response = table.get_item(
            Key={'file_id': file_id, 'sk': 'META'}
        )
        
        meta_item = meta_response.get('Item')
        if not meta_item:
            continue # Skip if the META item is somehow missing
            
        # Generate Presigned URLs (valid for 3600 seconds / 1 hour)
        thumb_url = None
        if 'thumb_url' in meta_item:
            thumb_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': BUCKET_NAME, 'Key': meta_item['thumb_url']},
                ExpiresIn=3600
            )
            
        file_url = None
        if 'file_url' in meta_item:
            file_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': BUCKET_NAME, 'Key': meta_item['file_url']},
                ExpiresIn=3600
            )
            
        # Build the result object
        results.append({
            'file_id': file_id,
            'file_type': meta_item.get('file_type'),
            'thumbnail_url': thumb_url,
            'file_url': file_url
        })
        
    # Return the final list of results
    return {
        'statusCode': 200,
        'headers': {'Access-Control-Allow-Origin': '*'},
        'body': json.dumps({'results': results})
    }

def handle_lex_request(event):
    # 1. Extract the species from Lex's payload
    slots = event.get('sessionState', {}).get('intent', {}).get('slots', {})
    species_slot = slots.get('Species')
    
    if not species_slot or not species_slot.get('value'):
        return build_lex_response("I didn't catch which animal you are looking for.")
        
    species = species_slot['value']['interpretedValue']
    
    # 2. Query your GSI for the species
    response = table.query(
        IndexName='tag-count-index',
        KeyConditionExpression=Key('tag').eq(species) & Key('tag_count').gte(1)
    )
    
    items = response.get('Items', [])
    
    if not items:
        return build_lex_response(f"I didn't find any photos of {species}.")
        
    # 3. Generate Presigned URLs for up to 3 matches
    urls = []
    for item in items[:3]: # Lex has a text limit, so we only show the first 3
        file_id = item['file_id']
        meta_res = table.get_item(Key={'file_id': file_id, 'sk': 'META'})
        meta = meta_res.get('Item', {})
        
        if 'file_url' in meta:
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': BUCKET_NAME, 'Key': meta['file_url']},
                ExpiresIn=3600
            )
            urls.append(url)

    if not urls:
        return build_lex_response(f"I found {len(items)} photos of {species}, but could not generate URLs for them.")

    # 4. Return the response with all links
    links_text = "\n".join(urls)
    msg = f"I found {len(items)} photos of {species}. Here are the links:\n{links_text}"
    return build_lex_response(msg)


def build_lex_response(message_text):
    # This is the exact format Lex expects to close the conversation
    return {
        "sessionState": {
            "dialogAction": {
                "type": "Close"
            },
            "intent": {
                "name": "FindSightings",
                "state": "Fulfilled"
            }
        },
        "messages": [
            {
                "contentType": "PlainText",
                "content": message_text
            }
        ]
    }