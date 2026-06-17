# Plan de Integracion de Pagos

## Objetivo

Integrar pagos con tarjeta de credito y debito usando Stripe, mantener el cobro en USD, desplegar la aplicacion en Hostinger VPS y asegurar que los codigos QR solo se generen cuando el pago sea confirmado por la pasarela.

## Decision recomendada

- Pasarela principal: Stripe
- Moneda: USD
- Hosting: Hostinger VPS
- Flujo recomendado: Stripe Checkout

## Por que Stripe Checkout

- Es mas rapido de integrar que una pasarela hecha completamente a medida.
- Reduce el riesgo tecnico al manejar tarjeta directamente.
- Sirve bien para una app Django como esta.
- Permite pasar de pruebas a produccion con menos friccion.

## Estado actual del proyecto

Ya existe:

- carrito
- checkout interno
- ordenes
- generacion de QR
- productos y codigos de reclamacion

Pero hoy el checkout marca la compra como completada dentro de la propia app. Para pagos reales eso debe cambiar.

## Regla de negocio clave

Los QR y codigos de reclamacion no deben generarse cuando el usuario da clic en pagar.

Solo deben generarse cuando Stripe confirme el pago de forma valida.

## Arquitectura recomendada de pago

### Estados de orden

Se recomienda que la orden tenga al menos estos estados:

- `PENDING`
- `PAID`
- `FAILED`
- `CANCELED`
- `REFUNDED`

Hoy la app ya usa estados de orden, pero antes de integrar Stripe hay que revisar si cubren bien el flujo real de pagos.

### Flujo correcto

1. Usuario arma carrito.
2. Usuario entra al checkout.
3. La app crea una orden temporal o pendiente.
4. La app crea una sesion de Stripe Checkout.
5. Stripe procesa el pago.
6. Stripe llama al webhook del servidor.
7. El servidor valida el webhook.
8. Si el pago fue exitoso:
   - marcar orden como `PAID`
   - generar tickets QR
   - generar codigos de reclamacion de productos si aplica
   - vaciar o convertir el carrito
9. Si el pago falla o se cancela:
   - dejar orden en `FAILED` o `CANCELED`
   - no generar QR

## Fases del trabajo

## Fase 1 - Preparacion tecnica local

### 1. Crear cuenta en Stripe

Se necesita:

- cuenta Stripe
- modo prueba habilitado
- `Publishable key`
- `Secret key`
- `Webhook secret`

### 2. Revisar el modelo de orden

Validar:

- si la orden actual soporta `PENDING`
- si hay que guardar `stripe_checkout_session_id`
- si hay que guardar `stripe_payment_intent_id`
- si hay que guardar fecha y resultado de pago

### 3. Separar pago de emision de QR

Hay que mover la generacion de:

- tickets
- token QR
- codigos de reclamacion

fuera del submit directo del checkout y llevarla al punto donde Stripe confirme el pago.

## Fase 2 - Integracion Stripe en local

### 4. Instalar SDK de Stripe

Agregar dependencia de Stripe al proyecto Django.

### 5. Crear variables de entorno

Agregar:

- `STRIPE_SECRET_KEY`
- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_CURRENCY=usd`

### 6. Crear endpoint para iniciar pago

Este endpoint debe:

- leer el carrito
- crear la orden pendiente
- crear sesion Stripe Checkout
- redirigir al checkout de Stripe

### 7. Crear pagina de exito y cancelacion

Se recomienda:

- `payment/success/`
- `payment/cancel/`

Importante:

- la pagina de exito no debe confiar por si sola en el navegador
- la confirmacion real debe venir del webhook

### 8. Crear webhook de Stripe

Este endpoint debe:

- validar firma del webhook
- escuchar eventos correctos
- por ejemplo `checkout.session.completed`
- confirmar monto y moneda
- marcar orden como pagada
- disparar la emision de QR y codigos de productos

## Fase 3 - Validaciones de negocio

### 9. Proteger duplicados

Se debe evitar:

- generar dos veces los mismos QR
- reprocesar el mismo webhook
- cobrar y crear dos ordenes por doble clic

Se recomienda idempotencia.

### 10. Revisar stock y disponibilidad

Antes de crear pago y al confirmar pago:

- validar existencia del evento
- validar que no haya finalizado
- validar stock de boletas
- validar stock de productos y variantes

### 11. Ajustar experiencia de usuario

La UI debe mostrar:

- estado de procesamiento
- pago exitoso
- pago cancelado
- pago fallido

El overlay con logo ya ayuda en el envio del checkout actual.

## Fase 4 - Despliegue en Hostinger

### 12. Usar VPS

Para Django y Python en Hostinger se recomienda VPS, no hosting web simple.

### 13. Preparar servidor

Configurar:

- Python
- dependencias
- PostgreSQL
- Gunicorn o equivalente
- Nginx o stack elegido
- SSL
- variables de entorno
- archivos estaticos
- media

### 14. Publicar dominio real

Se necesita dominio publico para:

- frontend real
- callback de Stripe
- webhook publico

### 15. Configurar webhook en Stripe

Con la URL real del servidor:

- registrar endpoint webhook en Stripe
- guardar `live webhook secret`

## Fase 5 - Paso a produccion

### 16. Cambiar de test a live

Reemplazar:

- claves test por live
- webhook secret test por live

### 17. Verificar cuenta de Stripe

Confirmar:

- identidad
- negocio
- cuenta bancaria
- datos fiscales si aplican

### 18. Pruebas finales reales

Probar:

- pago exitoso
- pago cancelado
- pago rechazado
- orden duplicada
- webhook repetido
- emision correcta de QR
- correo o pantalla de confirmacion si aplica

## Riesgos a evitar

- generar QR antes de confirmacion real
- confiar en el redirect del navegador como prueba de pago
- no validar webhook
- no guardar IDs de Stripe
- no controlar doble envio del formulario
- no controlar webhooks repetidos

## Orden recomendado de implementacion

1. ajustar modelo de orden y flujo interno
2. integrar Stripe en modo prueba
3. mover emision de QR al webhook
4. probar localmente
5. subir a Hostinger VPS
6. configurar webhook publico
7. pasar a produccion

## Checklist previo a empezar codigo

- crear cuenta Stripe
- definir si el negocio cobrara con cuenta de Estados Unidos
- confirmar que la moneda oficial sera USD
- confirmar que Hostinger sera VPS
- confirmar que usaremos Stripe Checkout y no Elements

## Recomendacion final

No esperar a subir a Hostinger para empezar la integracion.

Lo correcto es:

- desarrollar primero en local con Stripe test
- desplegar despues cuando el flujo ya este estable
- activar produccion al final

## Siguiente paso sugerido

Cuando se decida arrancar implementacion, el primer cambio tecnico debe ser:

`separar el checkout actual de la emision inmediata de QR y preparar ordenes pendientes para Stripe`

## Tareas de programacion

Esta seccion baja el plan a cambios concretos dentro del codigo.

## Bloque 1 - Refactor del checkout actual

### Tarea 1. Revisar el flujo actual de checkout

Archivos probables:

- `core/views.py`
- `templates/cart/checkout.html`
- `templates/cart/checkout_success.html`
- `core/services.py`

Objetivo:

- identificar exactamente donde hoy se crea la orden
- identificar donde hoy se generan tickets QR
- identificar donde hoy se generan codigos de reclamacion

Resultado esperado:

- mapa claro del flujo actual

### Tarea 2. Cambiar el flujo para soportar orden pendiente

Cambios:

- permitir estado `PENDING` en orden si aun no existe bien resuelto
- no marcar la orden como `PAID` al enviar checkout
- no generar QR en el submit directo

Resultado esperado:

- la app puede crear una orden pendiente sin emitir tickets

### Tarea 3. Agregar campos Stripe al modelo de orden

Campos recomendados:

- `stripe_checkout_session_id`
- `stripe_payment_intent_id`
- `payment_confirmed_at`
- `payment_provider`
- `payment_status_detail`

Resultado esperado:

- cada orden puede relacionarse con Stripe y trazarse correctamente

### Tarea 4. Crear migracion de base de datos

Cambios:

- nueva migracion para los campos de orden
- revisar compatibilidad con ordenes existentes

Resultado esperado:

- esquema listo para pagos reales

## Bloque 2 - Integracion base con Stripe

### Tarea 5. Instalar y configurar Stripe en Django

Cambios:

- agregar dependencia `stripe`
- agregar variables de entorno
- agregar lectura segura en `settings.py`

Variables:

- `STRIPE_SECRET_KEY`
- `STRIPE_PUBLISHABLE_KEY`
- `STRIPE_WEBHOOK_SECRET`
- `STRIPE_CURRENCY`

Resultado esperado:

- proyecto preparado para hablar con Stripe en test

### Tarea 6. Crear servicio interno para Stripe

Archivo recomendado:

- `core/stripe_service.py`

Responsabilidades:

- crear checkout session
- validar webhook
- extraer IDs y estados relevantes

Resultado esperado:

- logica de Stripe separada de la vista

### Tarea 7. Crear endpoint para iniciar pago Stripe

Ruta nueva recomendada:

- `cart/stripe/checkout/`

Responsabilidades:

- leer carrito activo
- validar items y stock
- crear orden `PENDING`
- crear session de Stripe Checkout
- redirigir al usuario a Stripe

Resultado esperado:

- el boton de pago deja de cerrar la compra dentro de Django y pasa a Stripe

### Tarea 8. Cambiar el boton actual de checkout

Cambios:

- el formulario de `Confirmar compra` debe apuntar al nuevo flujo Stripe
- mantener overlay de carga

Resultado esperado:

- UX igual de clara, pero con backend correcto

## Bloque 3 - Webhook y emision segura

### Tarea 9. Crear endpoint de webhook Stripe

Ruta recomendada:

- `payments/stripe/webhook/`

Responsabilidades:

- validar firma
- procesar `checkout.session.completed`
- opcionalmente procesar eventos de falla o expiracion

Resultado esperado:

- la app recibe confirmacion real del pago

### Tarea 10. Mover generacion de QR al webhook

Cambios:

- extraer la logica actual de emision de tickets a una funcion reutilizable
- esa funcion debe ejecutarse solo cuando el webhook marque la orden como pagada

Resultado esperado:

- no se generan QR antes del pago confirmado

### Tarea 11. Mover generacion de codigos de productos al webhook

Cambios:

- separar la logica de `ProductRedemption`
- crear esos codigos solo al confirmar pago

Resultado esperado:

- productos tambien quedan protegidos por confirmacion real

### Tarea 12. Hacer el webhook idempotente

Cambios:

- si Stripe reenvia el mismo evento, no duplicar tickets ni redemptions
- validar si la orden ya fue procesada

Resultado esperado:

- seguridad contra eventos repetidos

## Bloque 4 - Pantallas y estados del usuario

### Tarea 13. Crear pagina de pago exitoso

Ruta recomendada:

- `payments/success/`

Objetivo:

- informar que el pago fue recibido
- si el webhook ya proceso, mostrar acceso a boletas
- si aun no termina, mostrar mensaje de espera

Resultado esperado:

- UX clara despues de volver desde Stripe

### Tarea 14. Crear pagina de pago cancelado

Ruta recomendada:

- `payments/cancel/`

Objetivo:

- informar que no se completo el pago
- permitir volver al carrito

Resultado esperado:

- flujo limpio si el usuario cancela

### Tarea 15. Agregar mensajes de error de pago

Escenarios:

- evento ya no disponible
- stock agotado
- session invalida
- orden inexistente

Resultado esperado:

- errores comprensibles para usuario y admin

## Bloque 5 - Validaciones de negocio

### Tarea 16. Revalidar stock antes de crear session

Objetivo:

- no mandar a Stripe un pago por algo que ya no existe

### Tarea 17. Revalidar stock en confirmacion final

Objetivo:

- proteger carrera entre dos compradores simultaneos

### Tarea 18. Definir que pasa si cambia el stock durante el pago

Opciones a decidir en implementacion:

- reservar stock al crear orden pendiente
- o revalidar al final y rechazar si ya no hay disponibilidad

Mi recomendacion:

- reservar stock logicamente por un tiempo corto o bloquear por orden pendiente con expiracion

## Bloque 6 - Seguridad y configuracion

### Tarea 19. Guardar secretos fuera del codigo

Cambios:

- `.env`
- variables de entorno del VPS

Objetivo:

- no exponer claves de Stripe en repositorio

### Tarea 20. Preparar configuracion por ambiente

Ambientes:

- local test
- servidor test si aplica
- produccion

Objetivo:

- separar claves y URLs por entorno

## Bloque 7 - Pruebas automatizadas

### Tarea 21. Crear pruebas de orden pendiente

Validar:

- al iniciar pago no se generen tickets
- se cree orden `PENDING`

### Tarea 22. Crear pruebas del webhook exitoso

Validar:

- cambia a `PAID`
- genera QR
- genera codigos de productos

### Tarea 23. Crear pruebas de webhook repetido

Validar:

- no duplique orden
- no duplique tickets
- no duplique redemptions

### Tarea 24. Crear pruebas de cancelacion o fallo

Validar:

- no se generen QR
- orden quede en estado correcto

## Bloque 8 - Despliegue

### Tarea 25. Preparar servidor Hostinger VPS

Cambios:

- instalar dependencias
- configurar servicio web
- configurar SSL
- configurar variables de entorno

### Tarea 26. Configurar URL publica del webhook

Objetivo:

- conectar Stripe con dominio real

### Tarea 27. Probar end to end en produccion test

Validar:

- session Stripe
- retorno a la app
- webhook publico
- emision QR

## Orden recomendado de programacion

1. revisar flujo actual
2. refactor a orden pendiente
3. agregar campos Stripe al modelo
4. crear servicio Stripe
5. crear endpoint de inicio de pago
6. crear webhook
7. mover emision de QR y productos al webhook
8. crear pantallas success/cancel
9. agregar pruebas
10. desplegar en VPS

## Primera tarea tecnica real a ejecutar

Si empezamos a programar, la primera tarea correcta es:

`refactorizar checkout_cart para que deje de completar la compra directamente y prepare una orden pendiente reutilizable para Stripe`

## Estado actual de continuidad

Fecha de corte: `2026-06-04`

### Ya quedo implementado

- `Order` ya soporta:
  - `PENDING`
  - `PAID`
  - `FAILED`
  - `CANCELED`
  - `REFUNDED`
  - `VOID`
- `Order` ya guarda metadatos de Stripe:
  - `payment_provider`
  - `payment_status_detail`
  - `stripe_checkout_session_id`
  - `stripe_payment_intent_id`
  - `payment_confirmed_at`
- el checkout ya no entrega QR ni codigos de producto antes del pago
- el checkout crea o reutiliza una orden `PENDING`
- el boton del checkout ya redirige directo a `Stripe Checkout`
- el webhook `payments/stripe/webhook/` ya procesa `checkout.session.completed`
- el webhook ya es idempotente para no duplicar QR si Stripe reenvia el evento
- al confirmar pago:
  - la orden pasa a `PAID`
  - se generan boletas QR
  - se generan codigos de reclamacion de productos
  - el carrito pasa a `CONVERTED`
- la pantalla de retorno ya muestra:
  - `Pago confirmado` si la orden ya fue procesada
  - `Pago en revision` si el webhook aun no la marca como pagada
- si la compra incluye productos:
  - aparece boton `Ver productos y codigos`
  - la redireccion automatica va a `Mis compras y QR` filtrado por el evento

### Configuracion local ya usada

- Stripe CLI ya fue autenticada
- `.env` local ya tiene:
  - `STRIPE_PUBLISHABLE_KEY`
  - `STRIPE_SECRET_KEY`
  - `STRIPE_WEBHOOK_SECRET`
  - `STRIPE_CURRENCY=usd`
- webhook local probado con:
  - `stripe listen --events checkout.session.completed --forward-to localhost:8000/payments/stripe/webhook/`

### Verificaciones ya pasadas

- `python manage.py check`
- `python manage.py test core.tests.CheckoutFlowTests --keepdb`

Ultimo resultado validado:

- `17 tests OK`
- `check` sin errores

## Lo que falta para continuar

### Fase inmediata

1. Hacer pruebas manuales completas en local:
   - boleta sola
   - producto solo
   - boleta + producto
2. Confirmar visualmente:
   - pago exitoso
   - pago cancelado
   - QR generado
   - codigo de producto generado
   - redireccion final correcta

### Antes de Hostinger

3. Subir el proyecto a GitHub con una version limpia de esta fase
4. Revisar que no se suban secretos ni `.env`
5. Dejar claro el proceso de deploy para VPS

### En Hostinger

6. Montar VPS:
   - Ubuntu 22.04 LTS
   - Python
   - PostgreSQL
   - Gunicorn
   - Nginx
   - SSL
7. Configurar variables de entorno reales en el servidor
8. Publicar dominio y probar la app desplegada

### Stripe publico

9. Crear webhook publico real apuntando a:
   - `/payments/stripe/webhook/`
10. Guardar el nuevo `STRIPE_WEBHOOK_SECRET` del servidor
11. Probar Stripe en modo test ya desde el dominio publico

### Produccion real

12. Completar verificacion de cuenta Stripe
13. Cambiar claves `test` por `live`
14. Hacer una compra real pequena de validacion

## Punto exacto donde retomar

Cuando se retome, el siguiente bloque correcto es:

1. pruebas manuales completas del flujo en local
2. preparar subida a GitHub
3. preparar despliegue en Hostinger VPS

## Nota importante

El proyecto ya quedo bien encaminado para Stripe en `modo prueba`, pero todavia no debe considerarse listo para cobro real hasta:

- desplegarlo en entorno publico
- configurar webhook publico real
- probar pagos test desde el servidor
- cambiar a claves `live`
