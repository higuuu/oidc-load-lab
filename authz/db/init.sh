#!/bin/sh
set -eu

# Passwords are passed through stdin to psql and never placed in process argv.
psql --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE auth_user LOGIN PASSWORD '$AUTH_DB_PASSWORD';
CREATE ROLE app_user LOGIN PASSWORD '$APP_DB_PASSWORD';
CREATE ROLE authz_user LOGIN PASSWORD '$AUTHZ_DB_PASSWORD';
CREATE DATABASE auth OWNER auth_user;
CREATE DATABASE app OWNER app_user;
CREATE DATABASE authz OWNER authz_user;
SQL
