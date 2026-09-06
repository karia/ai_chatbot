data "archive_file" "dummy" {
  type        = "zip"
  output_path = "${path.module}/dummy.zip"
  source {
    content  = "def lambda_handler(event, context): return {'placeholder': True}"
    filename = "handler.py"
  }
}
resource "aws_iam_role" "verification" {
  name = "ai-chatbot-memory-verification"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}
resource "aws_cloudwatch_log_group" "verification" {
  name              = "/aws/lambda/ai-chatbot-memory-verification"
  retention_in_days = 1
}
resource "aws_iam_role_policy" "verification" {
  role = aws_iam_role.verification.id
  name = "verification"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${aws_cloudwatch_log_group.verification.arn}:*"
      },
      {
        Effect   = "Allow"
        Action   = ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:GetEvent", "bedrock-agentcore:ListEvents"]
        Resource = aws_bedrockagentcore_memory.verification.arn
      }
    ]
  })
}
resource "aws_lambda_function" "verification" {
  function_name = "ai-chatbot-memory-verification"
  role          = aws_iam_role.verification.arn
  filename      = data.archive_file.dummy.output_path
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"
  architectures = ["arm64"]
  depends_on    = [aws_iam_role_policy.verification]
  lifecycle {
    ignore_changes = [filename, source_code_hash, runtime, handler, description, timeout, memory_size, environment]
  }
}
