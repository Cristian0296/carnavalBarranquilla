# Tareas de Desarrollo (Arte + Rifa)

1. [x] Catalogo de obras
- Mostrar obras activas con precio por participacion en USD.
- Permitir crear/editar obras desde administracion.

2. [x] Compra por participaciones
- Seleccionar cantidad de participaciones por obra.
- Calcular total en backend (`precio_unitario_usd * cantidad`).
- Mostrar total estimado en pantalla de detalle.

3. [x] Snapshot economico por orden
- Guardar en `Order`: `unit_price_usd`, `quantity`, `total_usd`.
- Mantener historico aunque el precio de la obra cambie despues.

4. [x] Boletas QR de rifa
- Generar 1 boleta por cada participacion comprada.
- Mostrar boletas y QR en confirmacion de compra y en "Mis boletas de rifa".

5. [x] Recompras incrementales
- Permitir compras repetidas de la misma obra por el mismo usuario.
- Consolidar participaciones por obra en resumen de "Mis boletas".

6. [x] Regla de imagen unica por obra
- Limitar la carga a 1 imagen principal por obra.
- Bloquear nuevas cargas cuando la obra ya tenga imagen.

7. [x] Operacion y validacion
- Validar boletas de rifa por token/QR.
- Mantener bitacora de validaciones y control de doble uso.

8. [x] Ajustes de experiencia y copy
- Unificar textos en UI para "obras", "participaciones" y "boletas de rifa".
- Actualizar menu y panel administrativo con terminologia del nuevo dominio.

9. [x] Pruebas
- Verificar compra por cantidad, total USD y snapshot en orden.
- Verificar recompra incremental y conteo de boletas.
- Verificar limite de 1 imagen por obra.
