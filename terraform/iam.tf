resource "aws_iam_role" "app" {
  for_each             = toset(["ingress", "worker"])
  name                 = "${local.prefix}-${each.key}"
  permissions_boundary = aws_iam_policy.runtime_boundary.arn
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app" {
  for_each = aws_iam_role.app
  role     = each.value.id
  name     = "${local.prefix}-${each.key}"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.lambda[each.key].arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = each.key == "ingress" ? data.aws_secretsmanager_secret.signing.arn : data.aws_secretsmanager_secret.bot.arn
      },
      {
        Effect   = "Allow"
        Action   = each.key == "ingress" ? ["sqs:SendMessage"] : ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.events.arn
      }
      ], each.key == "worker" ? [
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents"]
        Resource = aws_bedrockagentcore_memory.conversation.arn
      }
      ] : [], each.key == "worker" && length(var.bedrock_resource_arns) > 0 ? [
      {
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
        Resource = sort(tolist(var.bedrock_resource_arns))
      }
    ] : [])
  })
}
