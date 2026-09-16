locals {
  alarm_metrics = {
    ingress-latency = {
      description         = "Ingress p99 latency reached two seconds."
      namespace           = "AWS/ApiGateway"
      metric_name         = "Latency"
      statistic           = null
      extended_statistic  = "p99"
      period              = 300
      threshold           = 2000
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions = {
        ApiId = aws_apigatewayv2_api.slack.id
        Stage = aws_apigatewayv2_stage.slack.name
      }
    }
    ingress-5xx = {
      description         = "Ingress returned at least one 5xx response in five minutes."
      namespace           = "AWS/ApiGateway"
      metric_name         = "5xx"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 300
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions = {
        ApiId = aws_apigatewayv2_api.slack.id
        Stage = aws_apigatewayv2_stage.slack.name
      }
    }
    queue-age = {
      description         = "The oldest queued event has waited more than five minutes."
      namespace           = "AWS/SQS"
      metric_name         = "ApproximateAgeOfOldestMessage"
      statistic           = "Maximum"
      extended_statistic  = null
      period              = 60
      threshold           = 300
      comparison_operator = "GreaterThanThreshold"
      dimensions          = { QueueName = aws_sqs_queue.events.name }
    }
    dlq-messages = {
      description         = "The dead-letter queue contains at least one event."
      namespace           = "AWS/SQS"
      metric_name         = "ApproximateNumberOfMessagesVisible"
      statistic           = "Maximum"
      extended_statistic  = null
      period              = 60
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions          = { QueueName = aws_sqs_queue.dlq.name }
    }
    needs-review = {
      description         = "At least one event entered NEEDS_REVIEW."
      namespace           = local.monitoring_namespace
      metric_name         = "NeedsReview"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 60
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions          = {}
    }
    lambda-timeouts = {
      description         = "A worker stopped before its Lambda execution budget expired."
      namespace           = local.monitoring_namespace
      metric_name         = "LambdaTimeouts"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 60
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions          = {}
    }
    session-stopped = {
      description         = "An event reached a session stopped for human review."
      namespace           = local.monitoring_namespace
      metric_name         = "SessionStopped"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 60
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions          = {}
    }
    ingress-errors = {
      description         = "Ingress Lambda recorded at least one error in five minutes."
      namespace           = "AWS/Lambda"
      metric_name         = "Errors"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 300
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions          = { FunctionName = aws_lambda_function.app["ingress"].function_name }
    }
    worker-errors = {
      description         = "Worker Lambda recorded at least one error in five minutes."
      namespace           = "AWS/Lambda"
      metric_name         = "Errors"
      statistic           = "Sum"
      extended_statistic  = null
      period              = 300
      threshold           = 1
      comparison_operator = "GreaterThanOrEqualToThreshold"
      dimensions          = { FunctionName = aws_lambda_function.app["worker"].function_name }
    }
  }
  monitoring_namespace = "${local.prefix}/Monitoring"
}

resource "aws_sns_topic" "alarms" {
  name = "${local.prefix}-alarms"
}

resource "aws_cloudwatch_log_metric_filter" "needs_review" {
  name           = "${local.prefix}-needs-review"
  log_group_name = aws_cloudwatch_log_group.lambda["worker"].name
  pattern        = "{ $.status = \"NEEDS_REVIEW\" }"
  metric_transformation {
    name          = "NeedsReview"
    namespace     = local.monitoring_namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "session_stopped" {
  name           = "${local.prefix}-session-stopped"
  log_group_name = aws_cloudwatch_log_group.lambda["worker"].name
  pattern        = "{ $.state = \"session_stopped\" }"
  metric_transformation {
    name          = "SessionStopped"
    namespace     = local.monitoring_namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "lambda_timeout" {
  name           = "${local.prefix}-lambda-timeout"
  log_group_name = aws_cloudwatch_log_group.lambda["worker"].name
  pattern        = "{ ($.state = \"budget_exhausted\") || ($.error_class = \"TimeoutError\") }"
  metric_transformation {
    name          = "LambdaTimeouts"
    namespace     = local.monitoring_namespace
    value         = "1"
    default_value = "0"
  }
}

resource "aws_cloudwatch_log_metric_filter" "service_throttled" {
  name           = "${local.prefix}-service-throttled"
  log_group_name = aws_cloudwatch_log_group.lambda["worker"].name
  pattern        = "{ $.operation = \"service_throttled\" }"
  metric_transformation {
    name      = "ServiceThrottles"
    namespace = local.monitoring_namespace
    value     = "1"
    dimensions = {
      Service = "$.service"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "monitoring" {
  for_each            = local.alarm_metrics
  alarm_name          = "${local.prefix}-${each.key}"
  alarm_description   = each.value.description
  namespace           = each.value.namespace
  metric_name         = each.value.metric_name
  statistic           = each.value.statistic
  extended_statistic  = each.value.extended_statistic
  period              = each.value.period
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  threshold           = each.value.threshold
  comparison_operator = each.value.comparison_operator
  dimensions          = each.value.dimensions
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
