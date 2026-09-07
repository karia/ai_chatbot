local config = std.parseJson(std.native('must_env')('APP_CONFIG')).ingress;
config + {
  Runtime: 'python3.14',
  Architectures: ['arm64'],
  Handler: 'app.lambda_handler',
  MemorySize: 256,
}
