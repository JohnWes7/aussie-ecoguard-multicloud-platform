import json
import os
import boto3

# Initialize Lex V2 client
lex_client = boto3.client('lexv2-runtime')

BOT_ID = os.environ.get('BOT_ID')
BOT_ALIAS_ID = os.environ.get('BOT_ALIAS_ID')
LOCALE_ID = "en_US"

def handler(event, context):
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid JSON body'})
        }

    text = body.get('text')
    session_id = body.get('sessionId', 'default-session')

    if not text:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing text'})
        }

    try:
        # Call Amazon Lex V2
        response = lex_client.recognize_text(
            botId=BOT_ID,
            botAliasId=BOT_ALIAS_ID,
            localeId=LOCALE_ID,
            sessionId=session_id,
            text=text
        )
        
        # Extract the text messages from Lex's response
        messages = response.get('messages', [])
        reply_text = "I'm sorry, I didn't understand that."
        
        if messages and len(messages) > 0:
            reply_text = messages[0].get('content', reply_text)
            
        return {
            'statusCode': 200,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({
                'message': reply_text,
                'intent': response.get('sessionState', {}).get('intent', {}).get('name')
            })
        }

    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Failed to communicate with Lex', 'details': str(e)})
        }
