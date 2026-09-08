output "slack_events_url" {
  value = "${aws_apigatewayv2_api.slack.api_endpoint}/slack/events"
}

output "app_config" {
  value = {
    for name, fn in aws_lambda_function.app : name => {
      FunctionName = fn.function_name
      Role         = aws_iam_role.app[name].arn
      Timeout      = name == "worker" ? var.worker_timeout : var.ingress_timeout
      Environment = { Variables = merge({
        LOG_LEVEL = var.log_level
        }, name == "ingress" ? {
        SLACK_TEAM_ID    = var.slack_team_id
        SLACK_API_APP_ID = var.slack_api_app_id
        QUEUE_URL        = aws_sqs_queue.events.url
        } : {
        BOT_TOKEN_SECRET_ARN = data.aws_secretsmanager_secret.bot.arn
        DYNAMODB_TABLE_NAME  = aws_dynamodb_table.state.name
        MEMORY_ID            = aws_bedrockagentcore_memory.conversation.id
      }) }
    }
  }
}
