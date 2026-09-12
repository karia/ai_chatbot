local config = std.parseJson(std.native('must_env')('APP_CONFIG')).ingress;
config + {
  Runtime: 'python3.14',
  Architectures: ['arm64'],
  Handler: 'app.lambda_handler',
  MemorySize: 256,
  Environment+: {
    Variables+: {
      SLACK_SIGNING_SECRET: std.native('must_env')('SLACK_SIGNING_SECRET'),
    },
  },
}
