#!/bin/bash
set -euo pipefail

# uv (Python dependency manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# pnpm (Node.js package manager)
npm install -g pnpm@10.34.5

# AWS CDK CLI
npm install -g aws-cdk

# Amplify Gen 2 CLI
npm install -g @aws-amplify/backend-cli

echo "================================================"
echo "  Setup complete!"
echo "  Next: sign in to AWS with"
echo "    aws login --remote"
echo "  Then check with: aws sts get-caller-identity"
echo "================================================"
