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

# ---------- Lex chatbot fulfilment (Spec 4.3) ----------
#
# The Lex V2 bot (AussieEcoGuardBot, set up in the console) calls this Lambda
# as its fulfilment code hook. Two intents:
#   FindSightings  "show me all photos of foxes spotted yesterday"
#                  slots Species, Count (optional), Date (optional, AMAZON.Date)
#   FindLostPet    "help me find a lost ginger cat"
#                  slots Species, Colour (optional)
# Free text is cleaned up here too, so the bot still works when a slot holds
# extra words ("fox photos"), when a slot is missing from the console setup,
# or when "lost"/"yesterday" only appear in the sentence (inputTranscript).
#
# The reply is one plain text message plus the matching files in the session
# attribute "results" (JSON, S3 keys only, newest first). The chat Lambda
# signs those keys and the website shows them as clickable thumbnails.

LEX_MAX_RESULTS = 12  # files sent back to the chat; the message says how many matched
LOCAL_TZ_NAME = "Australia/Melbourne"
try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo(LOCAL_TZ_NAME)
except Exception:  # no tz database: AEDT is close enough for "yesterday"
    LOCAL_TZ = timezone(timedelta(hours=11))

# Tags the model can give (infra/model/config.json "species").
SPECIES = [
    "fox", "rabbit", "feral pig", "cane toad", "cat", "dog",
    "deer", "goat", "horse", "camel", "cattle",
    "kangaroo", "possum", "wombat", "koala", "bandicoot", "echidna", "emu",
    "bird", "reptile", "frog", "rodent",
]
# Other words people use for those tags. Plurals ending in -s/-es are handled
# in species_from_text, so only irregular ones are listed.
SPECIES_WORDS = {
    "pig": "feral pig", "pigs": "feral pig", "wild pig": "feral pig", "boar": "feral pig",
    "toad": "cane toad", "toads": "cane toad",
    "kitten": "cat", "kittens": "cat", "kitty": "cat", "puppy": "dog", "puppies": "dog",
    "bunny": "rabbit", "bunnies": "rabbit", "roo": "kangaroo", "wallaby": "kangaroo",
    "wallabies": "kangaroo", "cow": "cattle", "cows": "cattle", "bull": "cattle",
    "deers": "deer", "geese": "bird", "snake": "reptile", "lizard": "reptile",
    "mouse": "rodent", "mice": "rodent", "rat": "rodent",
}
# The model's colours (config.json "colours"); ginger is how people say orange.
COLOUR_WORDS = {
    "orange": "orange", "ginger": "orange", "red": "orange", "black": "black",
    "white": "white", "grey": "grey", "gray": "grey", "brown": "brown",
}
FILLER = re.compile(
    r"\b(photos?|pictures?|pics?|images?|videos?|sightings?|files?|media|of|the|a|an|"
    r"some|all|any|my|me|show|find|search|spotted|seen|found|please|lost|missing)\b"
)
LOST_WORDS = re.compile(r"\b(lost|missing|ran away|run away|find my)\b")


def handle_lex_request(event):
    try:
        return fulfil(event)
    except Exception as err:  # never let Lex show the user an error
        print(f"Lex fulfilment failed: {err!r}")
        return lex_reply(event, "Sorry, something went wrong while searching. Please try again.")


def fulfil(event):
    intent = (event.get("sessionState") or {}).get("intent") or {}
    slots = intent.get("slots") or {}
    transcript = str(event.get("inputTranscript") or "").lower()

    species = species_from_text(slot_text(slots, "Species")) or species_from_text(transcript)
    if not species:
        heard = slot_text(slots, "Species")
        start = f"I don't know the animal \"{heard}\" yet. " if heard else ""
        return lex_reply(event, start + "Try one of: fox, cat, dog, rabbit, feral pig, cane toad, kangaroo or wombat.")

    lost = intent.get("name") == "FindLostPet" or bool(LOST_WORDS.search(transcript))
    colour = (colour_from_text(slot_text(slots, "Colour")) or colour_from_text(transcript)) if lost else None
    min_count = max(1, whole_number(slot_text(slots, "Count")) or 1)
    when = date_range(slot_text(slots, "Date")) or date_range_from_text(transcript)
    start, end, period = when if when else (None, None, "")

    found = collect_files(species, 1 if lost else min_count, {colour} if colour else None, start, end)

    if lost:
        what = f"{colour} {species}" if colour else species
        if not found:
            text = f"No sightings of a {what}{period} yet. I'd check again later, or subscribe to {species} alerts in the Notifications tab."
        else:
            text = f"I found {len(found)} sighting{'s' * (len(found) != 1)} of a {what}{period} that could be your pet, newest first."
    else:
        what = species if min_count == 1 else f"at least {min_count} {species}"
        if not found:
            text = f"I didn't find any photos of {what}{period}."
        else:
            text = f"I found {len(found)} file{'s' * (len(found) != 1)} with {what}{period}, newest first."
    if len(found) > LEX_MAX_RESULTS:
        text += f" Showing the newest {LEX_MAX_RESULTS}."

    results = [{
        "file_id": meta["file_id"],
        "filename": meta.get("filename", meta["file_id"][:12]),
        "file_type": meta.get("file_type"),
        "created_at": meta.get("created_at", ""),
        "thumb_key": meta.get("thumb_url"),
        "file_key": meta["file_url"],
    } for meta, _tags in found[:LEX_MAX_RESULTS]]
    return lex_reply(event, text, {"results": json.dumps(results), "species": species})


def slot_text(slots, name):
    """The slot's resolved value, or what the user typed, or ""."""
    value = ((slots or {}).get(name) or {}).get("value") or {}
    resolved = value.get("resolvedValues") or []
    return str(value.get("interpretedValue") or (resolved[0] if resolved else "") or value.get("originalValue") or "").strip()


def species_from_text(text):
    words = FILLER.sub(" ", str(text or "").lower())
    words = re.sub(r"[^a-z ]", " ", words).split()
    if not words:
        return None
    # Longest phrase first, so "feral pig" wins over "pig".
    for size in (2, 1):
        for i in range(len(words) - size + 1):
            phrase = " ".join(words[i:i + size])
            for candidate in (phrase, re.sub(r"(es|s)$", "", phrase), re.sub(r"s$", "", phrase)):
                if candidate in SPECIES:
                    return candidate
                if candidate in SPECIES_WORDS:
                    return SPECIES_WORDS[candidate]
    return None


def colour_from_text(text):
    for word in re.sub(r"[^a-z ]", " ", str(text or "").lower()).split():
        if word in COLOUR_WORDS:
            return COLOUR_WORDS[word]
    return None


def whole_number(text):
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    text = str(text or "").strip().lower()
    if text in words:
        return words[text]
    try:
        return int(text)
    except ValueError:
        return None


def date_range(value):
    """An AMAZON.Date value as (start, end, " <text>") in local time, or None.

    Lex resolves "yesterday" to 2026-11-01, "last week" to 2026-W44,
    "this weekend" to 2026-W44-WE and "in October" to 2026-10."""
    value = str(value or "").strip().upper()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=LOCAL_TZ)
            return day, day + timedelta(days=1), f" on {day:%-d %b %Y}"
        match = re.fullmatch(r"(\d{4})-W(\d{2})(-WE)?", value)
        if match:
            monday = datetime.strptime(f"{match[1]}-W{match[2]}-1", "%G-W%V-%u").replace(tzinfo=LOCAL_TZ)
            if match[3]:
                saturday = monday + timedelta(days=5)
                return saturday, saturday + timedelta(days=2), f" on the weekend of {saturday:%-d %b}"
            return monday, monday + timedelta(days=7), f" in the week of {monday:%-d %b}"
        if re.fullmatch(r"\d{4}-\d{2}", value):
            first = datetime.strptime(value, "%Y-%m").replace(tzinfo=LOCAL_TZ)
            following = (first + timedelta(days=32)).replace(day=1)
            return first, following, f" in {first:%B %Y}"
    except ValueError:
        return None
    return None


def date_range_from_text(text):
    """Common time words in the sentence, for a bot without a Date slot."""
    today = datetime.now(LOCAL_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    if "today" in text:
        return today, today + timedelta(days=1), " today"
    if "yesterday" in text:
        return today - timedelta(days=1), today, " yesterday"
    match = re.search(r"\b(?:last|past) (\d+) days?\b", text)
    if match:
        return today - timedelta(days=int(match[1]) - 1), today + timedelta(days=1), f" in the last {match[1]} days"
    if "this week" in text:
        monday = today - timedelta(days=today.weekday())
        return monday, today + timedelta(days=1), " this week"
    if "last week" in text:
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=7), " last week"
    if "this month" in text:
        return today.replace(day=1), today + timedelta(days=1), " this month"
    return None


def lex_reply(event, text, attributes=None):
    """Close the intent with one message, the format Lex V2 expects."""
    state = event.get("sessionState") or {}
    intent = dict(state.get("intent") or {})
    intent.setdefault("name", "FindSightings")
    intent["state"] = "Fulfilled"
    session_attributes = dict(state.get("sessionAttributes") or {})
    session_attributes.pop("results", None)  # never show the previous answer's files
    session_attributes.update(attributes or {})
    return {
        "sessionState": {
            "dialogAction": {"type": "Close"},
            "intent": intent,
            "sessionAttributes": session_attributes,
        },
        "messages": [{"contentType": "PlainText", "content": text}],
    }


def parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def collect_files(species, min_count=1, colours=None, start=None, end=None):
    """Every file (all users: public sightings) with at least min_count of species.

    colours: a set; the file needs at least one of them in its attributes.
    start/end: aware datetimes; created_at must be in [start, end).
    Returns [(meta, tags)] newest first."""
    file_ids = set()
    kwargs = {
        'IndexName': 'tag-count-index',
        'KeyConditionExpression': Key('tag').eq(species) & Key('tag_count').gte(min_count),
    }
    while True:
        page = table.query(**kwargs)
        file_ids.update(item['file_id'] for item in page.get('Items', []))
        if 'LastEvaluatedKey' not in page:
            break
        kwargs['ExclusiveStartKey'] = page['LastEvaluatedKey']

    found = []
    for file_id in file_ids:
        items = table.query(KeyConditionExpression=Key('file_id').eq(file_id)).get('Items', [])
        meta = next((item for item in items if item['sk'] == 'META'), None)
        if not meta or 'file_url' not in meta:
            continue
        attributes = {str(a).lower() for a in meta.get('attributes', [])}
        if colours and not colours & attributes:
            continue
        created = parse_time(meta.get('created_at', ''))
        if (start or end) and created is None:
            continue
        if (start and created < start) or (end and created >= end):
            continue
        tags = {item['tag']: int(item['tag_count']) for item in items if item['sk'].startswith('TAG#')}
        found.append((meta, tags))
    found.sort(key=lambda pair: pair[0].get('created_at', ''), reverse=True)
    return found


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

    results = [{
        'file_id': meta['file_id'],
        'filename': meta.get('filename', meta['file_id'][:12]),
        'file_type': meta.get('file_type'),
        'created_at': meta.get('created_at', ''),
        'attributes': [str(a).lower() for a in meta.get('attributes', [])],
        'tags': tags,
        'thumbnail_url': sign(meta['thumb_url']) if 'thumb_url' in meta else None,
        'file_url': sign(meta['file_url']),
    } for meta, tags in collect_files(species, 1, colours or None, since)]
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
