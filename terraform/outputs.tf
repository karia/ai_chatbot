output "lambda_function_name" {
  value = aws_lambda_function.verification.function_name
}
output "lambda_role_arn" {
  value = aws_iam_role.verification.arn
}
output "memory_id" {
  value = aws_bedrockagentcore_memory.verification.id
}
output "aws_region" {
  value = data.aws_region.current.region
}
