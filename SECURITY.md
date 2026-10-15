# Seguridad

Si encuentra una vulnerabilidad, **no abra un issue público**. Use
[Report a vulnerability](https://github.com/Gaaraalex/opennccm/security/advisories/new) (aviso privado de GitHub).

Recomendaciones de despliegue:

- Publique la interfaz solo en la red de gestión o detrás de una VPN.
- Reemplace el certificado autofirmado por uno de su CA.
- Respalde `/etc/opennccm/secret.key` fuera del servidor: sin ella los datos no se pueden descifrar.
- Use un usuario dedicado y con privilegios mínimos en los equipos de red.
- Mantenga OpenNCCM al día con `sudo opennccm update`.
