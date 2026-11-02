import json
import os
import boto3 
from boto3.dynamodb.conditions import Key

# Initialize clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')

TABLE_NAME = os.environ.get('TABLE_NAME')
BUCKET_NAME = os.environ.get('BUCKET_NAME')
table = dynamodb.Table(TABLE_NAME)

def handler(event, context):
    # 1. Parse the API Gateway event body
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return {
            'statusCode': 400,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Invalid JSON body'})
        }

    # Extract variables
    urls = body.get('urls', [])

    if not urls:
        return {
            'statusCode': 400,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Missing urls'})
        }

    file_ids = []
    for url in urls:
        raw_id = url.split('/')[-1].split('?')[0]
        file_ids.append(raw_id)


    # 1. Delete from S3
    try:
        for file_id in file_ids:
            # Delete the original file
            s3_client.delete_object(
                Bucket=BUCKET_NAME,
                Key=f'uploads/{file_id}'
            )
            
            # Delete the thumbnail
            s3_client.delete_object(
                Bucket=BUCKET_NAME,
                Key=f'thumbnails/{file_id}.jpg'
            )
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Failed to delete files from S3', 'details': str(e)})
        }

    # 2. Delete from DynamoDB
    try:
        for file_id in file_ids:
            # Query to find ALL items for this file (META and all TAG# items)
            response = table.query(
                KeyConditionExpression=Key('file_id').eq(file_id)
            )
            
            # Loop through the returned items and delete them one by one
            for item in response.get('Items', []):
                table.delete_item(
                    Key={
                        'file_id': item['file_id'],
                        'sk': item['sk']
                    }
                )
    except Exception as e:
        return {
            'statusCode': 500,
            'headers': {'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Failed to delete database records', 'details': str(e)})
        }

    return {
        'statusCode': 200,
        'headers': {'Access-Control-Allow-Origin': '*'},
        'body': json.dumps({'message': 'Files deleted successfully'})
    }
