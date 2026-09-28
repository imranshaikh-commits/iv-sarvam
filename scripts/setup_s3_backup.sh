#!/usr/bin/env bash
# One-time setup for off-host backups of the Shilpi database tables.
#
# Creates, in ap-south-1 (Mumbai, same region as the EC2 host):
#   - a private S3 bucket with versioning, default encryption, public access
#     blocked, and a 30-day expiry on old versions and on objects
#   - an IAM role the EC2 instance can assume, allowed to write only to that
#     bucket, attached to the instance as an instance profile
# The host then copies ~/sarvam-backups to the bucket nightly (see the cron
# line printed at the end), with no access keys stored on the server.
#
# Run from a machine with the AWS CLI logged in as an account admin:
#   bash scripts/setup_s3_backup.sh
# Safe to re-run: each step skips what already exists.
set -euo pipefail

REGION="${REGION:-ap-south-1}"
HOST_IP="${HOST_IP:-13.206.20.25}"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="${BUCKET:-iv-sarvam-backups-$ACCOUNT}"
ROLE="sarvam-ec2-backup"

echo "account $ACCOUNT, bucket $BUCKET, region $REGION"

if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --bucket "$BUCKET" --region "$REGION" \
    --create-bucket-configuration LocationConstraint="$REGION" >/dev/null
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration \
  '{"Rules":[{"ID":"expire-30d","Status":"Enabled","Filter":{},"Expiration":{"Days":30},"NoncurrentVersionExpiration":{"NoncurrentDays":30}}]}'

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' >/dev/null
fi
aws iam put-role-policy --role-name "$ROLE" --policy-name write-sarvam-backups --policy-document \
  "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"s3:ListBucket\"],\"Resource\":\"arn:aws:s3:::$BUCKET\"},{\"Effect\":\"Allow\",\"Action\":[\"s3:PutObject\",\"s3:GetObject\"],\"Resource\":\"arn:aws:s3:::$BUCKET/*\"}]}"

if ! aws iam get-instance-profile --instance-profile-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE" --role-name "$ROLE"
  sleep 10  # IAM propagation before the profile can be attached
fi

INSTANCE=$(aws ec2 describe-instances --region "$REGION" \
  --filters "Name=ip-address,Values=$HOST_IP" --query 'Reservations[0].Instances[0].InstanceId' --output text)
CURRENT=$(aws ec2 describe-iam-instance-profile-associations --region "$REGION" \
  --filters "Name=instance-id,Values=$INSTANCE" --query 'IamInstanceProfileAssociations[0].IamInstanceProfile.Arn' --output text)
if [ "$CURRENT" = "None" ]; then
  aws ec2 associate-iam-instance-profile --region "$REGION" --instance-id "$INSTANCE" \
    --iam-instance-profile Name="$ROLE" >/dev/null
  echo "attached $ROLE to $INSTANCE"
elif [[ "$CURRENT" != *"$ROLE"* ]]; then
  echo "NOTE: $INSTANCE already has profile $CURRENT; add the write-sarvam-backups policy to that role instead."
fi

cat <<EOF

Done. On the EC2 host:
  sudo snap install aws-cli --classic
  (crontab -l; echo '15 3 * * * /snap/bin/aws s3 sync \$HOME/sarvam-backups s3://$BUCKET/tables/ --only-show-errors >> \$HOME/sarvam-backup.log 2>&1') | crontab -
EOF
