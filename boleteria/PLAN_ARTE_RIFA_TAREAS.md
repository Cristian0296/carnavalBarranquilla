# Plan De Trabajo - Arte + Boletas De Rifa

Fecha: 2026-02-28
Estado: Completado
Moneda objetivo: USD

## Objetivo General
Transformar el software de venta de entradas a un sistema de compra de participaciones para rifa de obras de arte (1 obra = 1 imagen principal), donde cada participacion genera una boleta QR.

## Reglas De Negocio Acordadas
- La moneda es USD.
- El usuario puede comprar participaciones de forma incremental (comprar ahora y luego volver a comprar mas).
- El total de cada compra es `precio_unitario_usd * cantidad_participaciones`.
- Cada participacion genera una boleta QR unica.

## Backlog Priorizado (Ejecucion En Orden)

### Tarea 1 - Ajuste De Dominio Sin Romper Base Actual [COMPLETADA]
- Objetivo: mantener modelos actuales pero cambiar semantica funcional de "evento" a "obra".
- Entregables:
- Actualizar textos en vistas y plantillas principales para lenguaje de arte/rifa.
- Definir en el sistema que cada obra usa una imagen principal.
- Criterio de listo:
- Navegacion y pantallas muestran terminologia de obra/participacion/boleta de rifa.

### Tarea 2 - Precio En USD Por Obra [COMPLETADA]
- Objetivo: agregar precio unitario por participacion a cada obra.
- Entregables:
- Campo en modelo para precio unitario en USD.
- Formulario de creacion/edicion de obra con precio.
- Visualizacion del precio en lista y detalle.
- Criterio de listo:
- Admin puede crear/editar obra con precio USD.
- Usuario ve precio claramente.

### Tarea 3 - Compra Por Cantidad De Participaciones [COMPLETADA]
- Objetivo: permitir elegir cantidad de participaciones al comprar una obra.
- Entregables:
- Input de cantidad en detalle de obra.
- Validaciones de cantidad (minimo y maximo configurable).
- Calculo de total en backend.
- Criterio de listo:
- Si precio es 1 USD y cantidad 10, total de compra es 10 USD.

### Tarea 4 - Persistencia De Total Y Precio Historico En Orden [COMPLETADA]
- Objetivo: que cada orden guarde snapshot economico de la compra.
- Entregables:
- Campos en `Order` para `unit_price_usd`, `quantity`, `total_usd`.
- Backfill para ordenes existentes.
- Criterio de listo:
- Orden conserva valor historico aunque cambie el precio de la obra despues.

### Tarea 5 - Emision De Boletas QR Por Participacion [COMPLETADA]
- Objetivo: generar 1 boleta por cada participacion comprada.
- Entregables:
- Al comprar N participaciones se crean N boletas.
- Confirmacion de compra muestra total y boletas generadas.
- Criterio de listo:
- Conteo de boletas coincide con cantidad comprada.

### Tarea 6 - Compras Incrementales De La Misma Obra [COMPLETADA]
- Objetivo: permitir recompras de participaciones por el mismo usuario.
- Entregables:
- Mantener compras historicas separadas por orden.
- "Mis boletas" consolidado por obra y por fecha.
- Criterio de listo:
- Usuario puede comprar hoy 1 y manana 10; sistema refleja 11 boletas totales.

### Tarea 7 - Restricciones De Imagen (1 Obra = 1 Imagen) [COMPLETADA]
- Objetivo: alinear carga/gestion de imagen con regla de obra unica.
- Entregables:
- Limitar carga a una sola imagen principal por obra.
- Ajustar UI de carrusel a imagen unica.
- Criterio de listo:
- No se pueden asociar varias imagenes a una misma obra.

### Tarea 8 - Ajustes De Mis Boletas Para Rifa [COMPLETADA]
- Objetivo: mejorar lectura de boletas por participacion.
- Entregables:
- Etiquetas y textos de "boleta de rifa".
- Mostrar resumen por obra: participaciones totales del usuario.
- Criterio de listo:
- Usuario entiende rapido cuantas participaciones tiene por obra.

### Tarea 9 - Panel Administrativo De Boletas De Rifa [COMPLETADA]
- Objetivo: adaptar vistas internas al nuevo contexto.
- Entregables:
- Renombrar secciones admin a "Obras", "Boletas de rifa", "Participaciones".
- Filtros utiles por obra, usuario y estado.
- Criterio de listo:
- Admin opera boletas/obras sin referencias a eventos tradicionales.

### Tarea 10 - Pruebas End-To-End Del Nuevo Flujo [COMPLETADA]
- Objetivo: asegurar estabilidad funcional.
- Entregables:
- Tests de precio, cantidad, total, recompra incremental y emision de boletas.
- Actualizacion de tests antiguos que dependian del flujo de evento/entrada.
- Criterio de listo:
- Suite verde en funcionalidades criticas de compra y boletas de rifa.

### Tarea 11 - Limpieza De Copys Y Documentacion Final [COMPLETADA]
- Objetivo: dejar el proyecto consistente en lenguaje y documentacion.
- Entregables:
- Actualizar `TASKS.md`/SDD o documento equivalente.
- Revisar plantillas para eliminar texto heredado de "evento/entrada" cuando no aplique.
- Criterio de listo:
- Producto y docs alineados con "arte + rifa".

## Orden Recomendado De Ejecucion
1. Tarea 2
2. Tarea 3
3. Tarea 4
4. Tarea 5
5. Tarea 6
6. Tarea 7
7. Tarea 8
8. Tarea 9
9. Tarea 10
10. Tarea 11
11. Tarea 1

Nota: La Tarea 1 (copy/semantica visual global) se puede completar al inicio, pero se recomienda cerrarla al final para evitar retrabajo de textos durante cambios de flujo.
