#!/bin/bash

# Pulse Deployment Script
# Usage: ./deploy.sh [--check] [--tags tag1,tag2]

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
# Pulse keeps its Ansible tree in deploy/ (not ansible/ like the sibling
# projects). The script cd's into it before running so deploy/ansible.cfg
# (pipelining, inventory, stdout callback) is picked up.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/deploy"
PLAYBOOK="deploy.yml"
INVENTORY="inventory.yml"
VAULT_PASSWORD_FILE="${VAULT_PASSWORD_FILE:-$DEPLOY_DIR/vault_secret}"
BECOME_PASSWORD="${BECOME_PASSWORD}"
GITHUB_SSH_KEY_PATH="${GITHUB_SSH_KEY_PATH:-$HOME/.ssh/github_deploy_key}"

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
    if [[ ! -f "$DEPLOY_DIR/$PLAYBOOK" ]]; then
        log_error "Playbook not found: $DEPLOY_DIR/$PLAYBOOK"
        exit 1
    fi

    if [[ ! -f "$DEPLOY_DIR/$INVENTORY" ]]; then
        log_error "Inventory not found: $DEPLOY_DIR/$INVENTORY"
        exit 1
    fi

    # Secrets are inline ansible-vault strings in group_vars/all.yml, so a
    # missing/placeholder vault password fails decryption before anything
    # runs — error out instead of writing a dummy file.
    if [[ ! -f "$VAULT_PASSWORD_FILE" ]]; then
        log_error "Vault password file not found: $VAULT_PASSWORD_FILE"
        log_info "Create it with the vault password used to encrypt the inline"
        log_info "!vault strings in deploy/group_vars/all.yml, then chmod 600 it."
        exit 1
    fi

    # Check if GitHub SSH key exists — the preflight role copies it to the
    # VPS so the git module can clone the repo.
    if [[ ! -f "$GITHUB_SSH_KEY_PATH" ]]; then
        log_error "GitHub SSH key not found at: $GITHUB_SSH_KEY_PATH"
        log_info "This is the shared Axiolo deploy key (also used by"
        log_info "image-compressor, sitechecker, octoping). You can:"
        log_info "  1. Copy it from the machine that has it"
        log_info "  2. Or set GITHUB_SSH_KEY_PATH to point to your existing key"
        exit 1
    fi

    log_success "Requirements check passed"
}

warn_if_unpushed() {
    # Ansible deploys whatever is on origin/$BRANCH, not the working tree.
    if command -v git &> /dev/null && git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &> /dev/null; then
        local local_ref remote_ref
        local_ref=$(git -C "$SCRIPT_DIR" rev-parse --verify --quiet "refs/heads/$BRANCH" || true)
        remote_ref=$(git -C "$SCRIPT_DIR" rev-parse --verify --quiet "refs/remotes/origin/$BRANCH" || true)
        if [[ -n "$local_ref" && -n "$remote_ref" && "$local_ref" != "$remote_ref" ]]; then
            log_warning "Local '$BRANCH' differs from 'origin/$BRANCH' — the VPS pulls from origin."
            log_warning "Push first if you expect local commits to ship."
        fi
    fi
}

# Parse command line arguments
EXTRA_ARGS=()
CHECK_MODE=false
BRANCH="main"

while [[ $# -gt 0 ]]; do
    case $1 in
        --check)
            # Project convention: a dry run is always --check --diff so the
            # operator reads every diff before applying to the shared VPS.
            CHECK_MODE=true
            EXTRA_ARGS+=("--check" "--diff")
            shift
            ;;
        --diff)
            EXTRA_ARGS+=("--diff")
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
        --branch)
            BRANCH="$2"
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
            echo "  --check           Run in check mode (dry run, includes --diff)"
            echo "  --diff            Show diffs on a real run"
            echo "  --branch BRANCH   Deploy a specific branch (default: main)"
            echo "  --tags TAGS       Run only specific tags (comma-separated)"
            echo "  --limit HOSTS     Limit to specific hosts"
            echo "  --verbose         Verbose output"
            echo "  --help            Show this help"
            echo ""
            echo "ENVIRONMENT VARIABLES:"
            echo "  VAULT_PASSWORD_FILE  Path to vault password file (default: deploy/vault_secret)"
            echo "  BECOME_PASSWORD      Sudo password for remote host (default: prompt)"
            echo "  GITHUB_SSH_KEY_PATH  Path to GitHub SSH key (default: ~/.ssh/github_deploy_key)"
            echo ""
            echo "EXAMPLES:"
            echo "  $0                              # Full deployment"
            echo "  $0 --check                      # Dry run — read every diff first"
            echo "  $0 --branch feat/xyz            # Deploy specific branch"
            echo "  $0 --tags backend,frontend      # Deploy only specific roles"
            echo "  $0 --limit pulse-prod           # Limit to specific hosts"
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
    log_info "Starting Pulse deployment..."

    if [[ "$CHECK_MODE" == true ]]; then
        log_info "Running in CHECK MODE (dry run)"
    fi

    check_requirements
    warn_if_unpushed

    # Build ansible command
    ANSIBLE_CMD=(
        "ansible-playbook"
        "$PLAYBOOK"
        "-i" "$INVENTORY"
        "--vault-password-file=$VAULT_PASSWORD_FILE"
        "-e" "pulse_repo_branch=$BRANCH"
        "-e" "pulse_deploy_key_path=$GITHUB_SSH_KEY_PATH"
    )

    # Sudo on the shared VPS needs a password: pass it via BECOME_PASSWORD
    # or get prompted once.
    if [[ -n "$BECOME_PASSWORD" ]]; then
        ANSIBLE_CMD+=("-e" "ansible_become_pass=$BECOME_PASSWORD")
    else
        ANSIBLE_CMD+=("--ask-become-pass")
    fi

    # Add extra arguments
    ANSIBLE_CMD+=("${EXTRA_ARGS[@]}")

    log_info "Running command: ${ANSIBLE_CMD[*]}"

    # Execute ansible playbook from deploy/ so ansible.cfg applies
    cd "$DEPLOY_DIR"
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
