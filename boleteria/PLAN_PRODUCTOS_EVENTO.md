# Plan Productos Por Evento

## 1. Objetivo

Agregar productos opcionales asociados a un evento, para que el admin pueda vender articulos durante la compra del evento y el usuario pueda reclamarlos presencialmente con un codigo unico.

Esto no es una tienda general. Los productos viven dentro de un evento y se compran junto con boletas de ese mismo evento.

## 2. Principios

- El admin debe poder crear productos de forma facil mientras crea o edita un evento.
- El usuario debe poder comprar boletas con o sin productos.
- El usuario debe poder comprar varias unidades de un producto.
- Los productos pueden tener variantes, como talla o color.
- El stock debe controlarse por variante.
- La entrega se hace en el evento usando un codigo unico de reclamacion.
- El validador debe poder consultar el codigo y marcar los productos como entregados.

## 3. Flujo Admin

Al crear o editar un evento, el admin vera una seccion opcional:

`Vender productos en este evento`

Si el admin activa esa opcion, podra agregar uno o mas productos.

### 3.1 Producto

Datos del producto:

- Nombre
- Descripcion corta
- Imagen opcional
- Precio
- Estado activo/inactivo
- Tiene variantes: si/no

### 3.2 Producto Sin Variantes

Si el producto no tiene variantes, el admin solo ingresa:

- Stock general

Internamente el sistema puede manejarlo como una variante unica llamada `Unidad`, pero el usuario no necesita verla.

Ejemplo:

- Producto: Gorra Carnaval
- Precio: USD 12
- Stock: 50

### 3.3 Producto Con Variantes

Si el producto tiene variantes, el admin puede agregar cuantas variantes necesite.

Cada variante tendra:

- Nombre visible
- Stock

Ejemplo:

Producto: Camiseta Carnaval

- `S / Roja`, stock 10
- `M / Roja`, stock 15
- `L / Roja`, stock 8
- `S / Negra`, stock 5
- `M / Negra`, stock 12

Para el primer alcance, todas las variantes usan el precio del producto. Si luego se necesita, se puede agregar precio por variante.

## 4. Flujo Usuario

En la pagina del evento, el usuario vera:

- Boletas disponibles
- Productos disponibles para ese evento

El usuario podra:

- Comprar solo boletas
- Comprar boletas y productos
- Comprar varias unidades de un producto
- Comprar varios productos distintos
- No comprar productos

### 4.1 Producto Simple

Si el producto no tiene variantes, el usuario vera:

- Nombre
- Imagen
- Precio
- Stock disponible
- Cantidad
- Boton para agregar al carrito

### 4.2 Producto Con Variantes

Si el producto tiene variantes, el usuario vera:

- Nombre
- Imagen
- Precio
- Selector de variante
- Stock disponible de la variante seleccionada
- Cantidad
- Boton para agregar al carrito

Ejemplo:

- Variante: `M / Roja`
- Cantidad: 2

## 5. Carrito Y Checkout

El carrito debe soportar items de:

- Boletas
- Productos del evento

Reglas:

- Todos los items del carrito deben pertenecer al mismo evento.
- El usuario puede quitar productos sin afectar las boletas.
- El usuario puede actualizar cantidades.
- El checkout debe revalidar stock antes de confirmar.
- Al confirmar compra, se descuentan stocks de boletas y variantes de productos.

El resumen de checkout debe separar visualmente:

- Boletas
- Productos para reclamar en el evento

## 6. Codigo De Reclamacion

Si una orden contiene productos, el sistema genera un codigo unico de reclamacion.

Recomendacion inicial:

- Un solo codigo por orden de productos del evento.
- Ese codigo representa todos los productos comprados en esa orden.

Ejemplo:

Codigo: `PROD-8F3K2A`

Detalle:

- Camiseta Carnaval, `M / Roja`, cantidad 2
- Gorra Carnaval, `Unidad`, cantidad 1

## 7. Flujo Validador

Debe existir una pantalla para validar entrega de productos.

El validador puede:

- Escanear el QR/codigo
- Digitar el codigo manualmente
- Ver comprador
- Ver evento
- Ver productos y cantidades
- Ver estado pendiente/entregado
- Marcar como entregado

Si ya fue entregado, el sistema debe mostrar:

- Estado entregado
- Fecha y hora
- Usuario validador que entrego

## 8. Modelo Propuesto

### 8.1 Product

Producto asociado a un evento.

Campos:

- event
- name
- description
- image
- price_usd
- is_active
- has_variants
- created_at
- updated_at

### 8.2 ProductVariant

Variante vendible de un producto.

Campos:

- product
- name
- stock_total
- is_active
- created_at
- updated_at

Para productos simples se crea una variante `Unidad`.

### 8.3 CartItem

Debe soportar:

- item_type: `TICKET` o `PRODUCT`
- ticket_type opcional
- product_variant opcional
- quantity
- unit_price_usd

### 8.4 OrderItem

Debe conservar snapshot de lo comprado:

- item_type: `TICKET` o `PRODUCT`
- ticket_type opcional
- product_variant opcional
- item_name
- variant_name
- unit_price_usd
- quantity
- total_usd

### 8.5 ProductRedemption

Codigo de reclamacion de productos.

Campos:

- order
- user
- event
- code
- status: `PENDING` o `DELIVERED`
- delivered_by
- delivered_at
- created_at

## 9. Tareas Por Fases

### Fase 1. Modelo Y Migracion

- [x] Crear modelo `Product`.
- [x] Crear modelo `ProductVariant`.
- [x] Agregar soporte de producto en `CartItem`.
- [x] Agregar soporte de producto en `OrderItem`.
- [x] Crear modelo `ProductRedemption`.
- [x] Crear migracion.
- [x] Validar que `python manage.py check` pase.

### Fase 2. Admin De Productos En Evento

- [x] Agregar formularios para producto y variantes.
- [x] Permitir crear productos al crear evento.
- [x] Permitir editar productos al editar evento.
- [x] Permitir agregar variantes dinamicamente.
- [x] Permitir activar/desactivar productos.
- [x] Permitir activar/desactivar variantes.
- [x] Validar que el producto simple tenga stock.
- [x] Validar que el producto con variantes tenga al menos una variante activa con stock.

### Fase 3. Vista Publica En Evento

- [x] Mostrar productos activos en la pagina del evento.
- [x] Mostrar imagen, nombre, descripcion y precio.
- [x] Mostrar selector de variante cuando aplique.
- [x] Mostrar cantidad disponible.
- [x] Permitir agregar productos al carrito.
- [x] No permitir agregar variantes agotadas.
- [x] No mostrar productos inactivos.

### Fase 4. Carrito

- [x] Mostrar boletas y productos en el carrito.
- [x] Separar visualmente boletas y productos.
- [x] Permitir actualizar cantidad de productos.
- [x] Permitir quitar productos.
- [x] Revalidar stock por variante al actualizar cantidad.
- [x] Mantener regla de un solo evento por carrito.

### Fase 5. Checkout

- [x] Revalidar stock de boletas.
- [x] Revalidar stock de variantes de producto.
- [x] Crear `OrderItem` para cada producto comprado.
- [x] Descontar stock de variantes al confirmar.
- [x] Generar `ProductRedemption` si la orden contiene productos.
- [x] Mostrar codigo de reclamacion en compra confirmada.
- [x] Mantener generacion de QR de boletas.

### Fase 6. Mis Compras

- [x] Mostrar productos comprados junto al evento.
- [x] Mostrar codigo de reclamacion.
- [x] Mostrar estado pendiente/entregado.
- [x] Mostrar detalle de productos y cantidades.

### Fase 7. Validacion De Entrega

- [x] Crear ruta para validar codigo de productos.
- [x] Crear pantalla para validador.
- [x] Permitir buscar por codigo.
- [x] Mostrar comprador, evento, productos y cantidades.
- [x] Marcar productos como entregados.
- [x] Registrar validador y fecha de entrega.
- [x] Bloquear doble entrega.

### Fase 8. Pruebas

- [x] Test de crear producto simple.
- [x] Test de crear producto con variantes.
- [x] Test de agregar producto simple al carrito.
- [x] Test de agregar variante al carrito.
- [x] Test de stock insuficiente.
- [x] Test de checkout con boletas solamente.
- [x] Test de checkout con boletas y productos.
- [x] Test de generacion de codigo de reclamacion.
- [x] Test de validacion de entrega.
- [x] Test de bloqueo de doble entrega.

## 10. Decisiones Pendientes

- [ ] Definir si el codigo de reclamacion sera texto, QR o ambos.
- [ ] Definir si se permite comprar solo productos sin boletas.
- [ ] Definir si las variantes pueden tener precio distinto.
- [ ] Definir si se necesita entrega parcial en el futuro.
- [ ] Definir si los productos deben aparecer solo antes del evento o tambien durante el evento.
