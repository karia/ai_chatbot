mock_provider "aws" {
  mock_resource "aws_iam_policy" {
    defaults = { arn = format("arn:aws:iam::%012d:policy/test-boundary", 0) }
  }
}
mock_provider "archive" {}

variables {
  state_bucket                   = "test-state"
  slack_team_id                  = "TTEST"
  slack_api_app_id               = "ATEST"
  environment                    = "dev"
  log_level                      = "DEBUG"
  point_in_time_recovery_enabled = false
}

run "initial_settings" {
  command = plan

  assert {
    condition = (
      !contains(keys(output.app_config.ingress.Environment.Variables), "SLACK_SIGNING_SECRET") &&
      !contains(keys(output.app_config.ingress.Environment.Variables), "SIGNING_SECRET_ARN") &&
      output.app_config.ingress.Environment.Variables.SLACK_TEAM_ID == "TTEST" &&
      output.app_config.ingress.Environment.Variables.SLACK_API_APP_ID == "ATEST" &&
      aws_lambda_function.app["ingress"].timeout == 10 &&
      output.app_config.ingress.Timeout == 10 &&
      aws_lambda_function.app["ingress"].timeout * 1000 > aws_apigatewayv2_integration.ingress.timeout_milliseconds &&
      aws_apigatewayv2_integration.ingress.timeout_milliseconds == local.api_integration_timeout_milliseconds &&
      !aws_dynamodb_table.state.point_in_time_recovery[0].enabled &&
      aws_lambda_function.app["worker"].timeout == 300 &&
      aws_sqs_queue.events.visibility_timeout_seconds == 1800 &&
      aws_lambda_function.app["worker"].reserved_concurrent_executions == 5 &&
      aws_lambda_event_source_mapping.worker.scaling_config[0].maximum_concurrency == 5 &&
      aws_lambda_event_source_mapping.worker.batch_size == 1
      && output.app_config.worker.Environment.Variables.BEDROCK_MODEL_ID == "global.anthropic.claude-opus-5"
      && output.app_config.worker.Environment.Variables.ENVIRONMENT == "dev"
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

  assert {
    condition = (
      aws_sns_topic.alarms.name == "ai-chatbot-dev-alarms" &&
      alltrue([for alarm in aws_cloudwatch_metric_alarm.monitoring : length(alarm.alarm_actions) == 1]) &&
      aws_cloudwatch_metric_alarm.monitoring["ingress-latency"].extended_statistic == "p99" &&
      aws_cloudwatch_metric_alarm.monitoring["ingress-latency"].threshold == 2000 &&
      aws_cloudwatch_metric_alarm.monitoring["ingress-5xx"].threshold == 1 &&
      aws_cloudwatch_metric_alarm.monitoring["queue-age"].threshold == 300 &&
      aws_cloudwatch_metric_alarm.monitoring["dlq-messages"].threshold == 1 &&
      aws_cloudwatch_metric_alarm.monitoring["needs-review"].threshold == 1 &&
      aws_cloudwatch_metric_alarm.monitoring["lambda-timeouts"].threshold == 1 &&
      aws_cloudwatch_metric_alarm.monitoring["session-stopped"].threshold == 1
    )
    error_message = "Monitoring alarms must preserve ADR-001 thresholds and share one SNS topic."
  }

  assert {
    condition = (
      aws_cloudwatch_log_metric_filter.needs_review.pattern == "{ $.status = \"NEEDS_REVIEW\" }" &&
      aws_cloudwatch_log_metric_filter.session_stopped.pattern == "{ $.state = \"session_stopped\" }" &&
      aws_cloudwatch_log_metric_filter.lambda_timeout.pattern == "{ ($.state = \"budget_exhausted\") || ($.error_class = \"TimeoutError\") }" &&
      aws_cloudwatch_log_metric_filter.service_throttled.pattern == "{ $.operation = \"service_throttled\" }"
    )
    error_message = "Structured log filters must measure isolation, timeout and throttling events."
  }

  assert {
    condition = alltrue([
      for config in values(output.app_config) : config.TracingConfig.Mode == "PassThrough"
    ])
    error_message = "Lambda tracing must not automatically collect model input or output."
  }
}

run "global_bedrock_arns" {
  command = plan
  variables {
    bedrock_resource_arns = [
      format("arn:aws:bedrock:ap-northeast-1:%012d:inference-profile/global.anthropic.claude-opus-5", 0),
      "arn:aws:bedrock:::foundation-model/anthropic.claude-opus-5",
      "arn:aws:bedrock:ap-northeast-1::foundation-model/anthropic.claude-opus-5"
    ]
  }
}

run "reject_bedrock_wildcards" {
  command = plan
  variables {
    bedrock_resource_arns = ["arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-5"]
  }
  expect_failures = [var.bedrock_resource_arns]
}

run "production_isolation" {
  command = plan
  variables {
    environment                    = "prod"
    log_level                      = "INFO"
    point_in_time_recovery_enabled = true
  }
  assert {
    condition = (
      aws_lambda_function.app["worker"].function_name == "ai-chatbot-prod-worker" &&
      aws_dynamodb_table.state.point_in_time_recovery[0].enabled &&
      aws_sqs_queue.events.name == "ai-chatbot-prod-events.fifo" &&
      aws_dynamodb_table.state.name == "ai-chatbot-prod-state" &&
      aws_bedrockagentcore_memory.conversation.name == "ai_chatbot_prod_conversation" &&
      data.aws_secretsmanager_secret.bot.name == "ai-chatbot-prod/slack-bot-token"
    )
    error_message = "Production resource and secret names must be isolated from development."
  }
}

run "custom_ingress_timeout" {
  command = plan
  variables {
    ingress_timeout = 12
  }
  assert {
    condition = (
      aws_lambda_function.app["ingress"].timeout == 12 &&
      output.app_config.ingress.Timeout == 12 &&
      aws_apigatewayv2_integration.ingress.timeout_milliseconds == local.api_integration_timeout_milliseconds
    )
    error_message = "Ingress timeout must reach both Terraform and lambroll without changing the API timeout."
  }
}

run "reject_short_ingress_timeout" {
  command = plan
  variables {
    ingress_timeout = 5
  }
  expect_failures = [var.ingress_timeout]
}

run "secret_permissions" {
  command = apply
  plan_options {
    target = [aws_iam_role_policy.app]
  }

  assert {
    condition = (
      !strcontains(aws_iam_role_policy.app["ingress"].policy, "secretsmanager:GetSecretValue") &&
      strcontains(aws_iam_role_policy.app["worker"].policy, "secretsmanager:GetSecretValue")
    )
    error_message = "Only the worker may retrieve Secrets Manager values."
  }
}
