import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import SLACK_BOT_TOKEN

logger = logging.getLogger()
slack_client = WebClient(token=SLACK_BOT_TOKEN)

def handle_slack_event(slack_event):
    channel_id = slack_event['channel']
    user_id = slack_event['user']
    message = slack_event['text']
    thread_ts = slack_event.get('thread_ts', slack_event['ts'])

    # メッセージからボットメンションを除去
    message = message.split('>', 1)[-1].strip()

    return channel_id, user_id, message, thread_ts

def get_thread_history(channel_id, thread_ts):
    try:
        response = slack_client.conversations_replies(
            channel=channel_id,
            ts=thread_ts
        )
        return response['messages']
    except SlackApiError as e:
        logger.error(f"Error fetching thread history: {e}")
        return []

def send_slack_message(channel_id, text, thread_ts):
    try:
        slack_client.chat_postMessage(
            channel=channel_id,
            text=text,
            thread_ts=thread_ts
        )
    except SlackApiError as e:
        logger.error(f"Error sending message to Slack: {e}")
        raise
