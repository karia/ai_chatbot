local must_env = std.native('must_env');
{
  FunctionName: must_env('LAMBDA_FUNCTION_NAME'),
  Role: must_env('LAMBDA_ROLE_ARN'),
  Runtime: 'python3.14',
  Architectures: ['arm64'],
  Handler: 'handler.lambda_handler',
  Description: 'Temporary AgentCore Memory verification',
  MemorySize: 512,
  Timeout: 20,
  Environment: { Variables: {
    MEMORY_ID: must_env('MEMORY_ID'),
  } },
}
