#!/bin/bash
# Vault setup script — run once after helm install vault
# Sets up k8s auth, roles per agent, and secret paths

set -e

VAULT_ADDR="${VAULT_ADDR:-http://vault.vault.svc.cluster.local:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root}"  # dev mode root token

echo "Configuring Vault at $VAULT_ADDR"

# Enable k8s auth
vault auth enable kubernetes 2>/dev/null || true

# Configure k8s auth
vault write auth/kubernetes/config \
  kubernetes_host="https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT" \
  token_reviewer_jwt="$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" \
  kubernetes_ca_cert="$(cat /var/run/secrets/kubernetes.io/serviceaccount/ca.crt)"

# Enable kv-v2 secrets
vault secrets enable -path=secret kv-v2 2>/dev/null || true

# ── research_cve agent — GitHub write + Anthropic ────────────────────────────
vault write auth/kubernetes/role/research-cve \
  bound_service_account_names=agent-research-cve \
  bound_service_account_namespaces=agent-harness \
  policies=research-cve \
  ttl=1h

vault policy write research-cve - <<EOF
path "secret/data/agents/research-cve/*" { capabilities = ["read"] }
path "secret/data/shared/anthropic"      { capabilities = ["read"] }
EOF

# Store research_cve GitHub token (repo write scope)
# vault kv put secret/agents/research-cve/github token=<YOUR_GITHUB_WRITE_TOKEN>
echo "⚠️  Run: vault kv put secret/agents/research-cve/github token=<write_token>"

# ── pr_validator agent — GitHub read only ────────────────────────────────────
vault write auth/kubernetes/role/pr-validator \
  bound_service_account_names=agent-pr-validator \
  bound_service_account_namespaces=agent-harness \
  policies=pr-validator \
  ttl=1h

vault policy write pr-validator - <<EOF
path "secret/data/agents/pr-validator/github" { capabilities = ["read"] }
path "secret/data/shared/anthropic"           { capabilities = ["read"] }
EOF

# Store pr_validator GitHub token (checks read + comment only)
echo "⚠️  Run: vault kv put secret/agents/pr-validator/github token=<read_token>"

# Shared Anthropic key
echo "⚠️  Run: vault kv put secret/shared/anthropic key=<ANTHROPIC_API_KEY>"

echo "✅ Vault configured. Store secrets as indicated above."
