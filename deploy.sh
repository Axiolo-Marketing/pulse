#!/bin/bash

# Axiolo Pulse Deployment Script
#
# The playbook now clones the repo on the VPS and builds the Astro frontend
# remotely. There is no local build step. Make sure you have committed and
# pushed any changes you want deployed to {{ pulse_repo_branch }} on origin
# before running.
#
# Usage: ./deploy.sh [--check] [--tags tag1,tag2]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
PLAYBOOK="deploy/deploy.yml"
INVENTORY="deploy/inventory.yml"
VAULT_PASSWORD_FILE="${VAULT_PASSWORD_FILE:-deploy/vault_secret}"
BECOME_PASSWORD="${BECOME_PASSWORD}"

# Functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

check_requirements() {
    log_info "Checking requirements..."

    # Check if ansible is installed
    if ! command -v ansible-playbook &> /dev/null; then
        log_error "ansible-playbook is not installed. Please install Ansible."
        exit 1
    fi

    # Check if files exist
    if [[ ! -f "$PLAYBOOK" ]]; then
        log_error "Playbook not found: $PLAYBOOK"
        exit 1
    fi

    if [[ ! -f "$INVENTORY" ]]; then
        log_error "Inventory not found: $INVENTORY"
        exit 1
    fi

    if [[ ! -f "$VAULT_PASSWORD_FILE" ]]; then
        log_warning "Vault password file not found: $VAULT_PASSWORD_FILE"
        log_info "Creating default vault password file..."
        echo "your-vault-password-here" > "$VAULT_PASSWORD_FILE"
        chmod 600 "$VAULT_PASSWORD_FILE"
        log_warning "Please update the vault password in $VAULT_PASSWORD_FILE"
    fi

    log_success "Requirements check passed"
}

# Parse command line arguments
EXTRA_ARGS=()
CHECK_MODE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --check)
            CHECK_MODE=true
            EXTRA_ARGS+=("--check")
            shift
            ;;
        --tags)
            EXTRA_ARGS+=("--tags" "$2")
            shift 2
            ;;
        --limit)
            EXTRA_ARGS+=("--limit" "$2")
            shift 2
            ;;
        --verbose|-v)
            EXTRA_ARGS+=("-v")
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "OPTIONS:"
            echo "  --check           Run in check mode (dry run)"
            echo "  --tags TAGS       Run only specific tags (comma-separated)"
            echo "  --limit HOSTS     Limit to specific hosts"
            echo "  --verbose         Verbose output"
            echo "  --help            Show this help"
            echo ""
            echo "ENVIRONMENT VARIABLES:"
            echo "  VAULT_PASSWORD_FILE  Path to vault password file (default: deploy/vault_secret)"
            echo "  BECOME_PASSWORD      Sudo password for remote host"
            echo ""
            echo "EXAMPLES:"
            echo "  $0                              # Full deployment (clone + build + deploy on VPS)"
            echo "  $0 --check                     # Dry run"
            echo "  $0 --tags backend              # Deploy only backend changes"
            echo "  $0 --limit pulse-prod          # Deploy only to pulse-prod host"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Main execution
main() {
    log_info "Starting Axiolo Pulse deployment..."

    if [[ "$CHECK_MODE" == true ]]; then
        log_info "Running in CHECK MODE (dry run)"
    fi

    check_requirements

    # Build ansible command
    ANSIBLE_CMD=(
        "ansible-playbook"
        "$PLAYBOOK"
        "-i" "$INVENTORY"
        "--vault-password-file=$VAULT_PASSWORD_FILE"
    )

    # Add become password if provided
    if [[ -n "$BECOME_PASSWORD" ]]; then
        ANSIBLE_CMD+=("-e" "ansible_sudo_pass=$BECOME_PASSWORD")
    fi

    # Add extra arguments
    ANSIBLE_CMD+=("${EXTRA_ARGS[@]}")

    log_info "Running command: ${ANSIBLE_CMD[*]}"

    # Execute ansible playbook
    if "${ANSIBLE_CMD[@]}"; then
        if [[ "$CHECK_MODE" == true ]]; then
            log_success "Deployment check completed successfully!"
        else
            log_success "Deployment completed successfully!"
            log_info "Application should be available at: https://pulse.axiolo.com"
        fi
    else
        log_error "Deployment failed!"
        exit 1
    fi
}

# Run main function
main "$@"
