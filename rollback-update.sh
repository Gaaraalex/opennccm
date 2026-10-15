#!/usr/bin/env bash
# Compatibilidad con la versión 1.1: la vuelta atrás ahora es "sudo opennccm rollback".
exec /usr/local/bin/opennccm rollback "$@"
