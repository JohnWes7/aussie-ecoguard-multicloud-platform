import json
import os
import boto3

# Initialize DynamoDB resource (Lab style: Week 11)
dynamodb = boto3.resource('dynamodb')
TABLE_NAME = os.environ.get('TABLE_NAME')
table = dynamodb.Table(TABLE_NAME)

def handler(event, context):
    # 1. Parse the API Gateway event body
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid JSON body'})
        }

    # Extract variables from the parsed body
    urls = body.get('urls', [])
    operation = body.get('operation')
    tags = body.get('tags', {})

    # Basic validation to ensure required fields are present
    if not urls or operation is None or not tags:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Missing urls, operation, or tags'})
        }


    if operation not in [0, 1]:
        return {
            'statusCode': 400,
            'body': json.dumps({'error': 'Invalid operation. Must be 0 or 1.'})
        }

    
    file_ids = []
    for url in urls:
        # 1. Split by '/' to get the last part (e.g., "hash?query=1")
        # 2. Split by '?' and take the first part to get clean "hash"
        raw_id = url.split('/')[-1].split('?')[0]
        file_ids.append(raw_id)


    if operation == 0:
        # Remove logic
        try:
            for file_id in file_ids:
                for tag in tags.keys():
                    # The assignment says to safely ignore tags that aren't there.
                    # DynamoDB delete_item naturally does this without erroring.
                    table.delete_item(
                        Key={
                            'file_id': file_id,
                            'sk': f'TAG#{tag}'
                        }
                    )
        except Exception as e:
            return {
                'statusCode': 500,
                'body': json.dumps({'error': 'Failed to remove tags', 'details': str(e)})
            }

    elif operation == 1:
        # Add logic
        transact_items = []
        
        for file_id in file_ids:
            # 1. Condition: The file's META record must exist (Once per file)
            transact_items.append({
                'ConditionCheck': {
                    'TableName': TABLE_NAME,
                    'Key': {'file_id': file_id, 'sk': 'META'},
                    'ConditionExpression': 'attribute_exists(file_id)'
                }
            })
            
            for tag, count in tags.items():
                # 2. Action: Put the new TAG# item
                transact_items.append({
                    'Put': {
                        'TableName': TABLE_NAME,
                        'Item': {
                            'file_id': file_id,
                            'sk': f'TAG#{tag}',
                            'tag': tag,
                            'tag_count': count
                        }
                    }
                })
        
        # Execute the transaction (all or nothing)
        try:
            dynamodb.meta.client.transact_write_items(
                TransactItems=transact_items
            )
        except Exception as e:
            return {
                'statusCode': 400,
                'body': json.dumps({'error': 'Transaction failed. File may not exist.', 'details': str(e)})
            }

        
    # Default response
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Tags updated successfully'})
    }