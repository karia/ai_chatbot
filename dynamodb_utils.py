import boto3
import time
from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Attr
from config import DYNAMODB_TABLE_NAME

dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(DYNAMODB_TABLE_NAME)

def save_initial_event(event_id, user_id, channel_id, thread_ts, user_message):
    timestamp = int(time.time() * 1000)
    try:
        table.put_item(
            Item={
                'event_id': event_id,
                'user_id': user_id,
                'timestamp': timestamp,
                'channel_id': channel_id,
                'thread_ts': thread_ts,
                'user_message': user_message,
                'status': 'processing'
            },
            ConditionExpression='attribute_not_exists(event_id)'
        )
        print(f"Initial entry saved to DynamoDB: event_id={event_id}")
        return True
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(f"Duplicate event detected during initial save: {event_id}")
            return False
        else:
            raise

def update_event(event_id, ai_response):
    try:
        table.update_item(
            Key={'event_id': event_id},
            UpdateExpression="set ai_response = :r, #s = :c",
            ExpressionAttributeValues={
                ':r': ai_response,
                ':c': 'completed'
            },
            ExpressionAttributeNames={
                '#s': 'status'
            },
            ConditionExpression=Attr('status').eq('processing')
        )
        print(f"Event updated in DynamoDB: event_id={event_id}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ConditionalCheckFailedException':
            print(f"Event already processed: {event_id}")
        else:
            raise
