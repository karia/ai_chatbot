mock_provider "aws" {}
mock_provider "archive" {}

variables {
  environment = "dev"
  log_level   = "DEBUG"
}

run "initial_settings" {
  command = plan

  assert {
    condition = (
      aws_lambda_function.app["ingress"].timeout == 3 &&
      aws_lambda_function.app["worker"].timeout == 120 &&
      aws_sqs_queue.events.visibility_timeout_seconds == 720 &&
      aws_lambda_function.app["worker"].reserved_concurrent_executions == 5 &&
      aws_lambda_event_source_mapping.worker.scaling_config[0].maximum_concurrency == 5 &&
      aws_lambda_event_source_mapping.worker.batch_size == 1
    )
    error_message = "Lambda and queue settings must preserve ADR-001's initial contract."
  }

  assert {
    condition = (
      aws_sqs_queue.events.fifo_queue && aws_sqs_queue.dlq.fifo_queue &&
      !aws_sqs_queue.events.content_based_deduplication &&
      aws_sqs_queue.events.message_retention_seconds == 345600 &&
      aws_sqs_queue.dlq.message_retention_seconds == 1209600 &&
      aws_dynamodb_table.state.hash_key == "pk" &&
      aws_dynamodb_table.state.ttl[0].attribute_name == "expires_at" &&
      aws_bedrockagentcore_memory.conversation.event_expiry_duration == 30 &&
      alltrue([for group in aws_cloudwatch_log_group.lambda : group.retention_in_days == 14]) &&
      aws_cloudwatch_log_group.api.retention_in_days == 14
    )
    error_message = "FIFO ordering, storage keys and retention must match the application contract."
  }
}

run "production_isolation" {
  command = plan
  variables {
    environment = "prod"
    log_level   = "INFO"
  }
  assert {
    condition = (
      aws_lambda_function.app["worker"].function_name == "ai-chatbot-prod-worker" &&
      aws_sqs_queue.events.name == "ai-chatbot-prod-events.fifo" &&
      aws_dynamodb_table.state.name == "ai-chatbot-prod-state" &&
      aws_bedrockagentcore_memory.conversation.name == "ai_chatbot_prod_conversation" &&
      data.aws_secretsmanager_secret.signing.name == "ai-chatbot-prod/slack-signing-secret" &&
      data.aws_secretsmanager_secret.bot.name == "ai-chatbot-prod/slack-bot-token"
    )
    error_message = "Production resource and secret names must be isolated from development."
  }
}
