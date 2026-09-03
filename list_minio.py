import boto3

s3_client = boto3.client(
    's3',
    endpoint_url='http://localhost:9000',
    aws_access_key_id='minioadmin',
    aws_secret_access_key='minioadmin'
)

job_id = '8431516f05024ce3b8d4c21af3acc8b4'
prefix = f'jobs/{job_id}/'

response = s3_client.list_objects_v2(Bucket='dags', Prefix=prefix)

print('Objects in MinIO for this job:')
for obj in response.get('Contents', []):
    print(f"  {obj['Key']} ({obj['Size']} bytes)")
