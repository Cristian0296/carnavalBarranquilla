# Spec-Driven Development (SDD)

## Sistema Web de Boletas con QR Firmado (MVP Demo Local)

------------------------------------------------------------------------

## 1. Vision del Sistema

Sistema web desarrollado en Django para la gestion de eventos y emision
de entradas digitales con codigos QR firmados mediante HMAC.
El sistema permite:

- Publicar eventos (2 simulacro).
- Simular la compra de entradas.
- Emitir tickets con token firmado.
- Validar entradas pegando manualmente el token (MVP).
- Evitar reutilizacion de entradas bajo concurrencia.

------------------------------------------------------------------------

## 2. Alcance (MVP minimo viable para demo local)

### Incluye

- Registro/Login Cliente.
- Login Admin (credencial preconfigurada).
- Listado y detalle de eventos.
- Compra simulada (sin pago real).
- Emision de ticket con QR y token firmado.
- Pagina "Mis tickets".
- Validacion admin por pegado de token.
- Bloqueo de doble validacion bajo concurrencia.
- Auditoria basica de validacion.

### No Incluye

- Pasarela de pago real.
- Escaneo con camara.
- Docker.
- Multi-organizador.
- Envio automatico de tickets por correo.
- Reportes avanzados.
- Control de capacidad.

------------------------------------------------------------------------

## 3. Roles del Sistema

### Cliente

- Ver eventos.
- Comprar entradas (simulado).
- Ver y copiar token.
- Visualizar QR.

### Admin

- Ver eventos, ordenes y tickets.
- Validar tickets pegando token.
- Ver auditoria basica.

------------------------------------------------------------------------

## 4. Requerimientos Funcionales

### FR-01 Autenticacion Cliente

El sistema debe permitir registro e inicio de sesion para clientes.

### FR-02 Autenticacion Admin

El sistema debe permitir acceso administrativo con credenciales
preconfiguradas.

### FR-03 Listado de Eventos

Mostrar eventos activos con informacion basica.

### FR-04 Compra Simulada

Permitir generar una orden y emitir ticket en estado `UNUSED`.

### FR-05 Emision de Token Firmado

Generar un token unico firmado mediante HMAC. El QR representara dicho
token.

### FR-06 Mis Tickets

El cliente debe visualizar tickets con estado (`UNUSED`, `USED`, `VOID`).

### FR-07 Validacion por Token

El admin debe poder pegar el token y validar:

1. Firma correcta.
2. Existencia del ticket.
3. Estado del ticket.
4. Marcar `USED` si corresponde.

### FR-08 Evitar Doble Validacion

El sistema debe impedir que un mismo ticket sea validado mas de una vez
bajo concurrencia.

### FR-09 Precarga de Eventos

El sistema debe iniciar con dos eventos simulacro activos.

------------------------------------------------------------------------

## 5. Requerimientos No Funcionales

### NFR-01 Seguridad

- Contrasenas hasheadas.
- Proteccion CSRF.
- Clave HMAC fuera del codigo (variable de entorno).

### NFR-02 Integridad

- Orden `PAID` implica ticket emitido.
- Ticket `USED` implica registro de validacion.

### NFR-03 Rendimiento

Validacion menor a 300ms promedio bajo carga moderada local.

### NFR-04 Trazabilidad

Registrar quien valida y cuando (incluye resultado).

------------------------------------------------------------------------

## 6. Modelo de Datos (Conceptual)

- User (id, username/email, password_hash, role)
- Event (id, title, datetime, status)
- Order (id, user_id, event_id, status, created_at)
- Ticket (id, ticket_uuid, order_id, event_id, status, issued_at, used_at, token_ref)
- ValidationLog (id, ticket_id, admin_id, validated_at, outcome)

------------------------------------------------------------------------

## 7. Especificacion del Token

### Payload

- ticket_uuid
- event_id
- issued_at (timestamp)

### Firma

HMAC(secret_key, payload_serializado)

Formato conceptual: base64url(payload_json) + "." + base64url(signature)

Notas:
- `ticket_uuid` es un UUID real y se usa en el token.
- `token_ref` guarda el token completo (para demo).

------------------------------------------------------------------------

## 8. Flujos Principales

### Flujo Compra

Cliente autenticado -> Comprar -> Orden `PAID` -> Ticket `UNUSED` -> Token generado.

### Flujo Validacion

Admin pega token -> Validar firma -> Marcar `USED` -> Registrar auditoria.

### Flujo Reuso

Token ya usado -> Rechazar.

### Flujo Token Invalido

Firma incorrecta -> Rechazar.

------------------------------------------------------------------------

## 8.1 Reglas de Estado (Minimas)

- Ticket inicia en `UNUSED` al comprar.
- Solo pasa a `USED` cuando el admin valida correctamente.
- `VOID` existe pero no se usa en el MVP (no hay anulaciones).

## 8.2 Concurrencia (Minima)

La validacion debe ser atomica: actualizar a `USED` solo si el estado actual es `UNUSED`
dentro de una transaccion.

## 8.3 Autenticacion Admin

El admin es un superusuario de Django preconfigurado.

## 8.4 Zona Horaria

Se usa la configuracion por defecto de Django con `USE_TZ=True`.

------------------------------------------------------------------------

## 9. Pruebas y Evaluacion

### Funcionales

- Compra genera ticket `UNUSED`.
- Validacion cambia estado a `USED`.
- Reuso bloqueado.
- Token adulterado rechazado.

### Carga (Local, opcional)

- 20 validaciones simultaneas del mismo token.

### Metricas

- Latencia p95.
- Errores bajo carga.
- 0 doble validacion.

------------------------------------------------------------------------

## 10. Criterios de Aceptacion

El sistema cumple cuando:

- Todos los flujos funcionan segun especificacion.
- No existe doble validacion bajo concurrencia.
- Se registran auditorias correctamente.

------------------------------------------------------------------------

Fin del Documento SDD.
