import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote, urlparse

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

    # Other searches on the same route (Spec 4.3)
    if 'thumbnail_url' in body:
        return find_original(body['thumbnail_url'])
    if 'lost_pet' in body:
        return find_lost_pet(body['lost_pet'])

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


# ---------- Thumbnail URL -> original file (Spec 4.3) ----------
#
# Body: {"thumbnail_url": "https://<bucket>.s3.<region>.amazonaws.com/thumbnails/<sha256>.jpg?X-Amz-..."}
# The thumbnail Lambda always writes thumbnails/<sha256>.jpg, and the original
# is the file with that sha256, so the key alone is enough. A signed URL, an
# unsigned one or the bare key all work; an expired signature does not matter.

THUMB_KEY_RE = re.compile(r"(?:^|/)thumbnails/([0-9a-f]{64})\.jpg$")
URL_EXPIRES = 3600  # seconds the returned signed URLs stay valid


def find_original(thumbnail_url):
    if not isinstance(thumbnail_url, str) or not thumbnail_url.strip():
        return api_response(400, {'error': 'thumbnail_url must be a non-empty string'})

    path = unquote(urlparse(thumbnail_url.strip()).path)
    match = THUMB_KEY_RE.search(path.lower())
    if not match:
        return api_response(400, {'error': 'not a thumbnail URL (expected .../thumbnails/<sha256>.jpg)'})
    file_id = match.group(1)

    meta = table.get_item(Key={'file_id': file_id, 'sk': 'META'}).get('Item')
    if not meta or meta.get('status') != 'READY' or 'file_url' not in meta:
        return api_response(404, {'error': 'no file has this thumbnail'})

    return api_response(200, {
        'file_id': file_id,
        'filename': meta.get('filename', ''),
        'file_type': meta.get('file_type'),
        'file_url': sign(meta['file_url']),
        'thumbnail_url': sign(meta['thumb_url']) if 'thumb_url' in meta else None,
    })


# ---------- Lost pet search (Spec 4.3) ----------
#
# Body: {"lost_pet": {"species": "cat", "colours": ["orange"], "days": 30}}
#   species  required, e.g. "cat" or "dog" (any tag works)
#   colours  optional; a file matches if it has at least one of them. The model
#            only gives colours for cats and dogs (infra/model/config.json)
#   days     optional; only sightings uploaded in the last N days
# Every user's files are searched (public sightings), newest first.

def find_lost_pet(query):
    if not isinstance(query, dict):
        return api_response(400, {'error': 'lost_pet must be an object'})

    species = str(query.get('species', '')).strip().lower()
    species = PLURAL_MAP.get(species, species)
    if not species:
        return api_response(400, {'error': 'lost_pet.species is required'})

    colours = query.get('colours') or []
    if not isinstance(colours, list) or not all(isinstance(c, str) for c in colours):
        return api_response(400, {'error': 'lost_pet.colours must be a list of strings'})
    colours = {c.strip().lower() for c in colours if c.strip()}

    days = query.get('days')
    since = None
    if days not in (None, ''):
        try:
            days = int(days)
        except (TypeError, ValueError):
            return api_response(400, {'error': 'lost_pet.days must be a whole number'})
        if days < 1:
            return api_response(400, {'error': 'lost_pet.days must be at least 1'})
        since = datetime.now(timezone.utc) - timedelta(days=days)

    file_ids = set()
    kwargs = {
        'IndexName': 'tag-count-index',
        'KeyConditionExpression': Key('tag').eq(species) & Key('tag_count').gte(1),
    }
    while True:
        page = table.query(**kwargs)
        file_ids.update(item['file_id'] for item in page.get('Items', []))
        if 'LastEvaluatedKey' not in page:
            break
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']

    results = []
    for file_id in file_ids:
        items = table.query(KeyConditionExpression=Key('file_id').eq(file_id)).get('Items', [])
        meta = next((item for item in items if item['sk'] == 'META'), None)
        if not meta or 'file_url' not in meta:
            continue
        attributes = [str(a).lower() for a in meta.get('attributes', [])]
        if colours and not colours.intersection(attributes):
            continue
        created_at = meta.get('created_at', '')
        if since and (not created_at or datetime.fromisoformat(created_at) < since):
            continue
        results.append({
            'file_id': file_id,
            'filename': meta.get('filename', file_id[:12]),
            'file_type': meta.get('file_type'),
            'created_at': created_at,
            'attributes': attributes,
            'tags': {item['tag']: int(item['tag_count']) for item in items if item['sk'].startswith('TAG#')},
            'thumbnail_url': sign(meta['thumb_url']) if 'thumb_url' in meta else None,
            'file_url': sign(meta['file_url']),
        })

    results.sort(key=lambda r: r['created_at'], reverse=True)
    return api_response(200, {'results': results})


def sign(key):
    return s3_client.generate_presigned_url(
        'get_object', Params={'Bucket': BUCKET_NAME, 'Key': key}, ExpiresIn=URL_EXPIRES
    )


def api_response(status, body):
    return {
        'statusCode': status,
        'headers': {'Access-Control-Allow-Origin': '*', 'Content-Type': 'application/json'},
        'body': json.dumps(body),
    }
