# CI/CD Lab App

This repository contains a simple Flask application (frontend + backend), a Dockerfile, helper scripts, and a GitHub Actions workflow that demonstrates building an image, pushing to Amazon ECR via OIDC, and triggering a deploy on an EC2 instance using SSM.

Quick overview
- App: `app/` (Flask + static assets)
- Dockerfile: builds the Flask app image
- scripts/: local helper scripts (`build.sh`, `push.sh`)
- deploy/run_deploy.sh: script intended to be placed on the EC2 instance (e.g. `/opt/deploy/run_deploy.sh`) and executed via SSM
- .github/workflows/deploy.yml: example workflow (contains placeholders you must update)

Before using the workflow
1. Create an ECR repository named `my-repo` (or update `.github/workflows/deploy.yml`).
2. Attach the `EC2AppRole` instance profile to the EC2 instance and install Docker + SSM agent.
3. Place `deploy/run_deploy.sh` on the EC2 host at `/opt/deploy/run_deploy.sh` and make it executable. The workflow will upload a `.env` file to `/opt/deploy/.env` from the GitHub secret named `APP_ENV` (see below).
4. Add the required GitHub repository secrets — see **Step 8** below for one-line `gh` CLI commands for each secret:
	- `OIDC_ROLE_ARN` — the role ARN for `GitHubActionsECRRole` (e.g. arn:aws:iam::123456789012:role/GitHubActionsECRRole)
	- `AWS_ACCOUNT_ID` — your AWS account ID
	- `AWS_REGION` — AWS region where ECR and EC2 live (e.g. us-east-1)
	- `INSTANCE_ID` — the EC2 instance id to deploy to (e.g. i-0123456789abcdef0)
	- `APP_ENV` — *(optional)* (multiline) the contents of your `.env` file (KEY=VALUE lines). This will be written to `/opt/deploy/.env` on the instance by the workflow.

Notes on secrets and local development
- For local runs, copy `.env.example` to `.env` and edit values locally.
- For CI, set `APP_ENV` in the repo secrets to the same KEY=VALUE lines you would put in `.env` (newline-separated). The workflow uploads this secret to the instance before calling the deploy script.
- Keep secrets out of the repo. `APP_ENV` is stored in GitHub Secrets and injected into the workflow masked from logs.

Local test
```bash
./scripts/build.sh my-app
# tag returned from git or 'latest' then push manually using push.sh
```

# Steps

Run all commands in **[AWS CloudShell](https://console.aws.amazon.com/cloudshell)** or any terminal with the AWS CLI and `gh` CLI installed and configured.

---

## 0. Set shared variables

Fill in your values once — every command below uses them.

```bash
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REGION=us-east-1          # change to your target region
export GITHUB_ORG=your-username  # GitHub username or org
export REPO_NAME=your-repo-name  # GitHub repository name
```

---

## 1. Create the GitHub repository

```bash
gh repo create "${REPO_NAME}" --public --clone
cd "${REPO_NAME}"
# copy your project files in, then:
git add .
git commit -m "Initial commit"
git push
```

---

## 2. Register the GitHub Actions OIDC provider

> Only needs to be done **once per AWS account**. Skip if it already exists.

The thumbprint is the same for all users and is based on GitHub's OIDC certificate. To find the current thumbprint, run:

```bash
# Find the current GitHub OIDC thumbprint
GITHUB_THUMBPRINT=$(echo | openssl s_client -servername token.actions.githubusercontent.com -connect token.actions.githubusercontent.com:443 2>/dev/null | openssl x509 -noout -fingerprint -sha1 | cut -d'=' -f2 | tr -d ':' | tr '[:upper:]' '[:lower:]')

aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list ${GITHUB_THUMBPRINT}
```

---

## 3. Create the `GitHubActionsECRRole` IAM role

GitHub Actions assumes this role (via OIDC) to push images to ECR and trigger SSM deploys.

```bash
# Write the trust policy
cat > /tmp/github-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::${ACCOUNT_ID}:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
        },
        "StringLike": {
          "token.actions.githubusercontent.com:sub": "repo:${GITHUB_ORG}/${REPO_NAME}:*"
        }
      }
    }
  ]
}
EOF

aws iam create-role \
  --role-name GitHubActionsECRRole \
  --assume-role-policy-document file:///tmp/github-trust-policy.json

# Allow pushing/pulling ECR images
aws iam attach-role-policy \
  --role-name GitHubActionsECRRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser

# Allow sending SSM commands to the EC2 instance
cat > /tmp/ssm-send-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["ssm:SendCommand", "ssm:GetCommandInvocation"],
      "Resource": "*"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name GitHubActionsECRRole \
  --policy-name SSMSendCommand \
  --policy-document file:///tmp/ssm-send-policy.json

# Print the role ARN — save this, you will need it for the GITHUB_OIDC_ROLE_ARN secret
aws iam get-role \
  --role-name GitHubActionsECRRole \
  --query Role.Arn --output text
```

---

## 4. Create the `EC2AppRole` IAM role and instance profile

The EC2 instance uses this role to pull images from ECR and to be managed by SSM.

```bash
cat > /tmp/ec2-trust-policy.json << EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
  --role-name EC2AppRole \
  --assume-role-policy-document file:///tmp/ec2-trust-policy.json

aws iam attach-role-policy \
  --role-name EC2AppRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly

aws iam attach-role-policy \
  --role-name EC2AppRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

# Create the instance profile and attach the role to it
aws iam create-instance-profile \
  --instance-profile-name EC2AppRole

aws iam add-role-to-instance-profile \
  --instance-profile-name EC2AppRole \
  --role-name EC2AppRole
```

---

## 5. Create the ECR repository

```bash
aws ecr create-repository \
  --repository-name my-repo \
  --region "${REGION}"
```

---

## 6. Use an EC2 instance

**If you already have a running Linux instance**, skip to "Attach the role" below.

**If you need to launch a new instance**, use the commands at the bottom of this section.

### Attach the role to your instance

```bash
export INSTANCE_ID=i-0123456789abcdef0   # your existing instance ID
export REGION=us-east-1                  # your region

aws ec2 associate-iam-instance-profile \
  --instance-id "${INSTANCE_ID}" \
  --region "${REGION}" \
  --iam-instance-profile Name=EC2AppRole
```

### Install required packages and SSM agent

Connect to your instance (via Session Manager, SSH, or EC2 console):

```bash
aws ssm start-session --target "${INSTANCE_ID}" --region "${REGION}"
```

Then run the appropriate commands for your Linux distro:

**Amazon Linux 2 / Amazon Linux 2023:**
```bash
sudo yum update -y
sudo yum install -y docker amazon-ssm-agent
sudo systemctl enable --now docker amazon-ssm-agent
sudo usermod -aG docker ec2-user
mkdir -p /opt/deploy
```

**Ubuntu / Debian:**
```bash
sudo apt update
sudo apt install -y docker.io awscliv2
sudo snap install amazon-ssm-agent --classic
sudo snap start amazon-ssm-agent
sudo systemctl enable --now docker
sudo usermod -aG docker ubuntu
mkdir -p /opt/deploy
```



Exit the session:
```bash
exit
```

### (Optional) Launch a new instance

If you don't have an instance yet, use these commands:

```bash
# Look up the default VPC and one of its subnets
VPC_ID=$(aws ec2 describe-vpcs \
  --filters "Name=isDefault,Values=true" \
  --region "${REGION}" \
  --query "Vpcs[0].VpcId" --output text)

SUBNET_ID=$(aws ec2 describe-subnets \
  --filters "Name=vpc-id,Values=${VPC_ID}" \
  --region "${REGION}" \
  --query "Subnets[0].SubnetId" --output text)

# Create a security group that allows HTTP traffic on port 8000
SG_ID=$(aws ec2 create-security-group \
  --group-name ci-cd-app-sg \
  --description "CI/CD app security group" \
  --vpc-id "${VPC_ID}" \
  --region "${REGION}" \
  --query GroupId --output text)

aws ec2 authorize-security-group-ingress \
  --group-id "${SG_ID}" \
  --protocol tcp --port 8000 --cidr 0.0.0.0/0 \
  --region "${REGION}"

# Get the latest Amazon Linux 2023 AMI
AMI_ID=$(aws ec2 describe-images \
  --owners amazon \
  --filters "Name=name,Values=al2023-ami-*-x86_64" "Name=state,Values=available" \
  --region "${REGION}" \
  --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)

# User data: install Docker and create the deploy directory
cat > /tmp/user-data.sh << 'USERDATA'
#!/bin/bash
dnf update -y
dnf install -y docker
systemctl enable --now docker
usermod -aG docker ec2-user
mkdir -p /opt/deploy
USERDATA

INSTANCE_ID=$(aws ec2 run-instances \
  --image-id "${AMI_ID}" \
  --instance-type t2.micro \
  --subnet-id "${SUBNET_ID}" \
  --security-group-ids "${SG_ID}" \
  --iam-instance-profile Name=EC2AppRole \
  --user-data file:///tmp/user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=ci-cd-app}]' \
  --region "${REGION}" \
  --query "Instances[0].InstanceId" --output text)

echo "Instance ID: ${INSTANCE_ID}"
export INSTANCE_ID

# Wait until the instance is in the running state before continuing
aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}" --region "${REGION}"
echo "Instance is running."
```

---

## 7. Create the deploy script on the instance

Wait ~60 seconds after launch for the SSM agent to register, then connect to your instance and create the script:

```bash
# Open an interactive shell on your instance
aws ssm start-session --target "${INSTANCE_ID}" --region "${REGION}"
```

Once connected, create the deploy script:

```bash
mkdir -p /opt/deploy
sudo vi /opt/deploy/run_deploy.sh
```

Paste the contents from below.
```bash
#!/usr/bin/env bash
set -euo pipefail

# This script is intended to live on the EC2 instance (e.g. /opt/deploy/run_deploy.sh)
# Usage: run_deploy.sh <aws_account_id> <region> <repo> <tag>
AWS_ACCOUNT_ID=${1}
REGION=${2}
REPO=${3}
TAG=${4}

REPO_URI=${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}

echo "Logging into ECR..."
aws ecr get-login-password --region ${REGION} | docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com

echo "Pulling image ${REPO_URI}:${TAG}"
docker pull ${REPO_URI}:${TAG}

echo "Stopping previous container if exists"
docker stop app || true
docker rm app || true

echo "Starting container"
docker run -d --name app -p 8000:8000 --env-file /opt/deploy/.env ${REPO_URI}:${TAG}
```



Save:
1. Hit `Esc` Key
2. Type `:wq` and `Enter`

Make it executable:
```bash
sudo chmod +x /opt/deploy/run_deploy.sh
exit
```

---

## 8. Set GitHub repository secrets

**Via CLI (if you have `gh` installed):**

```bash
gh secret set OIDC_ROLE_ARN \
  --body "$(aws iam get-role --role-name GitHubActionsECRRole --query Role.Arn --output text)"

gh secret set AWS_ACCOUNT_ID --body "${ACCOUNT_ID}"
gh secret set AWS_REGION     --body "${REGION}"
gh secret set INSTANCE_ID    --body "${INSTANCE_ID}"

# Create a .env file with your KEY=VALUE app environment variables, then:
gh secret set APP_ENV < .env
```

**Via GitHub UI (manual):**

Go to: **Settings → Secrets and variables → Actions → New repository secret**

Add these 5 secrets:

| Secret Name | Value |
|---|---|
| `OIDC_ROLE_ARN` | Output from: `aws iam get-role --role-name GitHubActionsECRRole --query Role.Arn --output text` |
| `AWS_ACCOUNT_ID` | Output from: `aws sts get-caller-identity --query Account --output text` |
| `AWS_REGION` | `us-east-1` (or your region) |
| `INSTANCE_ID` | `(your EC2 instance ID)` |
| `APP_ENV` | Paste your `.env` file contents (multiline, one `KEY=VALUE` per line) |

Your Secrets page should look like this:

```
Repository secrets

OIDC_ROLE_ARN              Updated 2 minutes ago
AWS_ACCOUNT_ID             Updated 2 minutes ago
AWS_REGION                 Updated 2 minutes ago
INSTANCE_ID                Updated 2 minutes ago
APP_ENV                    Updated 1 minute ago
```

> **Note:** `APP_ENV` is optional for testing. If you skip it, the workflow will still deploy successfully, just without environment variables injected into the container. Add it later if your app needs configuration.

---

Push to `main` and the workflow will build the Docker image, push it to ECR, and deploy it to your EC2 instance automatically.