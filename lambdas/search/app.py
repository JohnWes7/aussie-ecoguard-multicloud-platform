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
# Global plural mapping for both Lex and Browser searches
PLURAL_MAP = {
    "foxes": "fox",
    "dogs": "dog",
    "cats": "cat",
    "feral pigs": "feral pig",
    "cane toads": "cane toad",
    "rabbits": "rabbit",
    "kangaroos": "kangaroo"
}

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
    raw_tags = body.get('tags', {})
    
    # Normalize tags (lowercase and plural mapping)
    search_tags = {}
    for k, v in raw_tags.items():
        k_lower = k.lower()
        search_tags[PLURAL_MAP.get(k_lower, k_lower)] = v

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
        # Query ALL items for this file (META and TAG#) in one request
        file_items = table.query(
            KeyConditionExpression=Key('file_id').eq(file_id)
        ).get('Items', [])
        
        meta_item = next((item for item in file_items if item['sk'] == 'META'), None)
        if not meta_item:
            continue
            
        # Extract tags from the TAG# items
        tags = {item['tag']: int(item['tag_count']) for item in file_items if item['sk'].startswith('TAG#')}
            
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
            'filename': meta_item.get('filename', file_id[:12]), # Add filename
            'file_type': meta_item.get('file_type'),
            'thumbnail_url': thumb_url,
            'file_url': file_url,
            'tags': tags # Add tags
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
        
    species = species_slot['value']['interpretedValue'].lower()
    
    # Use the global plural map
    if species in PLURAL_MAP:
        species = PLURAL_MAP[species]
        
    # Extract the Count slot (default to 1 if not provided)
    count_slot = slots.get('Count')
    min_count = 1
    if count_slot and count_slot.get('value'):
        try:
            min_count = int(count_slot['value']['interpretedValue'])
        except ValueError:
            min_count = 1
            
    # 2. Query your GSI for the species with the dynamic count
    response = table.query(
        IndexName='tag-count-index',
        KeyConditionExpression=Key('tag').eq(species) & Key('tag_count').gte(min_count)
    )
    
    items = response.get('Items', [])
    
    if not items:
        return build_lex_response(f"I didn't find any photos of {species}.")
        
    # Return a single clickable link to the frontend
    frontend_url = f"https://aussie-ecoguard-web-908203539695.s3.ap-southeast-2.amazonaws.com/index.html?q={species}"
    
    msg = f"I found {len(items)} photos of {species}. <a href='{frontend_url}' target='_blank'>Click here to view them!</a>"
    
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