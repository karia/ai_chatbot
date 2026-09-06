local config = std.parseJson(std.native('must_env')('APP_CONFIG')).worker;
config + {
  Runtime: 'python3.14',
  Architectures: ['arm64'],
  Handler: 'app.lambda_handler',
  MemorySize: 512,
}
