# Design System - Boletas QR

## Direccion visual
- Estilo: hibrido premium (corporativo limpio + enfoque en eventos).
- Objetivo: interfaz clara, elegante y operable en movil/escritorio.

## Colores (tokens)
- `brand-50`: fondo suave principal.
- `brand-100`: badges suaves y acentos.
- `brand-500`: color de marca base.
- `brand-700`: CTA principal.
- `brand-900`: titulos y estados de alta jerarquia.
- `slate-200/300/700`: bordes, campos y texto secundario.
- `emerald-*`: exito.
- `amber-*`: advertencias.
- `red-*`: acciones destructivas.

## Tipografia
- Titulos: `text-2xl` con peso `font-semibold`.
- Subtitulos: `text-lg font-semibold`.
- Texto base: `text-sm` o `text-base` segun densidad.
- Identificadores tecnicos (`uuid`, `token`): `font-mono text-xs`.

## Espaciado y layout
- Contenedor principal: `max-w-6xl`.
- Secciones internas: `rounded-xl` o `rounded-2xl`.
- Gap estandar entre bloques: `mt-4` / `mt-6`.
- Grid responsive para acciones y cards: `sm:` y `md:` como base.

## Componentes reutilizables
- Botones:
  - `btn-primary`: accion principal.
  - `btn-danger`: accion destructiva.
  - `btn-neutral`: accion secundaria.
- Enlaces:
  - `link-primary`: enlaces de navegacion contextual.
- Contenedores:
  - `panel`: tarjeta principal con sombra.
  - `panel-soft`: tarjeta sin sombra fuerte.

## Formularios
- Labels visibles siempre.
- Inputs con borde `slate` y foco fuerte.
- Errores en `.errorlist`.
- Estados de ayuda con texto pequeno (`text-xs`) y contraste suficiente.

## Tablas
- Usar `caption` (puede ser `sr-only`) para accesibilidad.
- Encabezados con `scope="col"` y primera celda util con `scope="row"`.
- En movil: `overflow-x-auto` + `min-w-[...]`.
- Datos largos: `break-all` o `break-words`.

## Accesibilidad
- `skip link` al contenido principal.
- Foco visible reforzado para enlaces, botones y campos.
- Mensajes globales y de resultado con `aria-live`.
- Popups con `role="dialog"` y `aria-modal="true"`.

## Checklist rapido UI (previo demo)
1. Contraste visible en botones/enlaces.
2. Navegacion por teclado sin bloqueos.
3. Formularios con labels y errores claros.
4. Tablas legibles en movil con scroll horizontal.
5. Estados de exito/error visibles y comprensibles.
