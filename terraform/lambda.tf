data "archive_file" "dummy" {
  type        = "zip"
  output_path = "${path.module}/dummy.zip"
  source {
    content  = "def lambda_handler(event, context): raise RuntimeError('Application not deployed')"
    filename = "app.py"
  }
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(["ingress", "worker"])
  name              = "/aws/lambda/${local.prefix}-${each.key}"
  retention_in_days = 14
}

resource "aws_lambda_function" "app" {
  for_each                       = toset(["ingress", "worker"])
  function_name                  = "${local.prefix}-${each.key}"
  role                           = aws_iam_role.app[each.key].arn
  filename                       = data.archive_file.dummy.output_path
  runtime                        = "python3.14"
  handler                        = "app.lambda_handler"
  architectures                  = ["arm64"]
  timeout                        = each.key == "worker" ? var.worker_timeout : 3
  reserved_concurrent_executions = each.key == "worker" ? var.worker_concurrency : -1
  depends_on                     = [aws_iam_role_policy.app, aws_cloudwatch_log_group.lambda]
  lifecycle {
    ignore_changes = [filename, source_code_hash, runtime, handler, architectures, description, timeout, memory_size, environment, layers, ephemeral_storage, logging_config, tracing_config]
  }
}

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn = aws_sqs_queue.events.arn
  function_name    = aws_lambda_function.app["worker"].arn
  batch_size       = 1
  scaling_config {
    maximum_concurrency = var.worker_concurrency
  }
}
