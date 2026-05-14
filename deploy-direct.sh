#!/bin/bash
set -e

# Load deployment config from .env.deploy (gitignored — never committed)
# Copy .env.deploy.example to .env.deploy and fill in your values.
if [ ! -f ".env.deploy" ]; then
    echo "ERROR: .env.deploy not found. Copy .env.deploy.example and fill in your values."
    exit 1
fi
# shellcheck source=.env.deploy
source .env.deploy

TMP_DIR="/tmp"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Direct Deployment Pipeline (Registry-Bypass)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "Step 1: Transfer Images to ${DEPLOYMENT_HOST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Transfer image files via SCP
echo "Transferring maker8 image..."
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$TMP_DIR/maker8-${MAKER8_TAG}.tar.gz" \
    "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}:$COMPOSE_DIR/images/"

echo "Transferring editor8-backend image..."
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$TMP_DIR/editor8-backend-${EDITOR8_BACKEND_TAG}.tar.gz" \
    "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}:$COMPOSE_DIR/images/"

echo "Transferring editor8-frontend image..."
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$TMP_DIR/editor8-frontend-${EDITOR8_FRONTEND_TAG}.tar.gz" \
    "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}:$COMPOSE_DIR/images/"

echo "Images transferred"

echo ""
echo "Step 2: Load & Deploy on ${DEPLOYMENT_HOST}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# SSH into deployment host and load images
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "${DEPLOYMENT_USER}@${DEPLOYMENT_HOST}" << EOSSH
    set -e
    cd $COMPOSE_DIR

    echo "Loading maker8 image..."
    docker load -i images/maker8-${MAKER8_TAG}.tar.gz

    echo "Loading editor8-backend image..."
    docker load -i images/editor8-backend-${EDITOR8_BACKEND_TAG}.tar.gz

    echo "Loading editor8-frontend image..."
    docker load -i images/editor8-frontend-${EDITOR8_FRONTEND_TAG}.tar.gz

    echo "All images loaded"

    echo ""
    echo "Restarting services..."

    # Stop old services
    docker-compose down maker8 editor8-backend editor8-worker editor8-frontend 2>/dev/null || true
    sleep 2

    # Start new services
    docker-compose up -d maker8
    docker-compose up -d editor8-backend editor8-worker editor8-frontend

    sleep 5
    echo "Services restarted successfully"

    echo ""
    echo "Service Status:"
    docker-compose ps maker8 editor8-backend editor8-worker editor8-frontend

    echo ""
    echo "Waiting for health checks (30s)..."
    sleep 30
    docker-compose ps maker8 editor8-backend editor8-worker editor8-frontend || true

    # Cleanup old image files
    rm -f images/*.tar.gz

EOSSH

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Direct Deployment Complete!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Deployed Versions:"
echo "  maker8:           ${MAKER8_TAG}"
echo "  editor8-backend:  ${EDITOR8_BACKEND_TAG}"
echo "  editor8-worker:   ${EDITOR8_BACKEND_TAG}"
echo "  editor8-frontend: ${EDITOR8_FRONTEND_TAG}"
echo ""
