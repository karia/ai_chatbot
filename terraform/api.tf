resource "aws_apigatewayv2_api" "slack" {
  name          = "${local.prefix}-slack"
  protocol_type = "HTTP"
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.prefix}-slack"
  retention_in_days = 14
}

resource "aws_apigatewayv2_stage" "slack" {
  api_id      = aws_apigatewayv2_api.slack.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit = var.api_burst_limit
    throttling_rate_limit  = var.api_rate_limit
  }
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      responseLength = "$context.responseLength"
      latency        = "$context.responseLatency"
    })
  }
}

resource "aws_apigatewayv2_integration" "ingress" {
  api_id                 = aws_apigatewayv2_api.slack.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_alias.current["ingress"].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = local.api_integration_timeout_milliseconds
}

resource "aws_apigatewayv2_route" "events" {
  api_id    = aws_apigatewayv2_api.slack.id
  route_key = "POST /slack/events"
  target    = "integrations/${aws_apigatewayv2_integration.ingress.id}"
}

resource "aws_lambda_permission" "ingress" {
  statement_id  = "AllowSlackEvents"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app["ingress"].function_name
  qualifier     = aws_lambda_alias.current["ingress"].name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.slack.execution_arn}/*/POST/slack/events"
}
