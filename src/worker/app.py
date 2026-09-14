if __package__:
    from .pipeline import process
else:
    from pipeline import process


def lambda_handler(event, context):
    process(event, context)
