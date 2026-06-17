# Plan De Registro Y Confirmacion De Correo

## Objetivo

Definir un flujo de registro seguro desde el inicio para una plataforma que maneja boletas y otros objetos digitales comprados.

## Decision Tomada

- No usar login con Google en esta etapa.
- Mantener registro normal por formulario.
- Hacer obligatoria la confirmacion de correo desde el registro.
- Bloquear el inicio de sesion hasta que el usuario confirme su correo.
- Usar confirmacion por enlace, no por codigo manual.
- Mantener protecciones anti abuso aunque exista verificacion por email.

## Motivo De La Decision

Como el sistema entrega objetos digitales comprados, conviene validar desde el principio que el correo existe y pertenece al usuario.

Eso mejora:

- seguridad sobre las cuentas
- recuperacion de acceso
- limpieza de la base de usuarios
- entrega correcta de comunicaciones importantes
- control frente a correos mal escritos o falsos

## Flujo Definido

1. El usuario completa el formulario de registro.
2. El sistema crea la cuenta como no verificada.
3. El sistema envia un correo con enlace unico de confirmacion.
4. El usuario no puede iniciar sesion mientras no confirme el correo.
5. Si abre el enlace valido, la cuenta pasa a verificada.
6. Despues de confirmar, se redirige a `Inicio`.
7. En `Inicio` se muestra un mensaje de confirmacion exitosa.

## Reglas Cerradas

### Campos obligatorios en registro

- username o nombre de usuario
- correo electronico
- contrasena
- confirmacion de contrasena

### Campos opcionales por ahora

- foto de perfil
- telefono
- otros datos de perfil

### Politica de acceso

- el usuario puede enviar el formulario de registro
- la cuenta queda creada pero sin acceso hasta verificar correo
- el login se bloquea si la cuenta aun no ha confirmado el email
- el sistema debe mostrar un mensaje claro explicando que primero debe confirmar su correo

## Correo De Confirmacion

### Formato

- correo HTML con imagen de marca
- boton principal para confirmar
- enlace de respaldo visible
- version texto plano como alternativa

### Remitente

- `Carnaval de Barranquilla ATL <no-reply@atlcarnavaldebarranquilla.com>`

### Comportamiento del enlace

- debe ser unico y seguro
- debe expirar
- al usarse correctamente debe marcar la cuenta como verificada
- despues debe redirigir a `Inicio`

## Seguridad Minima Antes De Produccion

- rate limiting en registro
- rate limiting en login
- validacion fuerte de contrasena
- mensajes de error controlados
- logs basicos de registro e inicio de sesion

## Politica Recomendada Para El Lanzamiento

### Imprescindible

- confirmacion obligatoria por correo en registro
- bloqueo de login hasta verificar
- correo obligatorio y unico
- contrasena validada con reglas fuertes
- rate limiting en signup
- rate limiting en login
- logs basicos de seguridad

### Muy recomendable

- pantalla o mensaje claro de `Revisa tu correo`
- opcion de reenviar enlace de confirmacion
- expiracion razonable del enlace
- proteccion tambien en endpoints sensibles relacionados con acceso
- revision de mensajes para no revelar demasiada informacion a atacantes

### Puede esperar

- captcha
- politicas mas avanzadas anti abuso
- obligar verificacion adicional para otras acciones sensibles

### Mala idea posponer

- dejar signup sin limites
- dejar login sin limites
- permitir login de cuentas no verificadas
- no registrar eventos basicos de seguridad
- devolver mensajes demasiado explicitos en autenticacion

## Parametros Cerrados Para Implementar

- expiracion del enlace: `24 horas`
- reenvios permitidos: `3 por hora`
- mensaje al intentar entrar sin verificar: `Tu cuenta aun no ha sido verificada. Revisa tu correo y confirma tu cuenta para poder ingresar.`
- mensaje de exito al confirmar: `Tu correo fue confirmado correctamente. Ya puedes iniciar sesion.`

## Recuperacion De Contrasena

La recuperacion de contrasena por correo sigue siendo parte del flujo oficial y es coherente con esta decision.

- el correo ya queda validado desde el registro
- eso hace mas confiable el flujo de `Olvide mi contrasena`
- el mismo remitente corporativo puede usarse para ambos casos

## Resumen

La decision actual es:

- verificar correo desde el registro
- bloquear login hasta confirmar
- usar enlace de confirmacion
- redirigir a `Inicio` al confirmar
- mantener endurecimiento basico de acceso antes de produccion
