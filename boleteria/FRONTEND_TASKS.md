# Plan Frontend (Tailwind) - Sistema de Boletas/Entradas

## Objetivo
Redisenar la interfaz para que se vea elegante, moderna y clara, manteniendo intacto el backend actual.

## Enfoque Visual Elegido
- Estilo: `Hibrido Premium` (base corporativa + detalles modernos de eventos).
- Framework CSS: `Tailwind CSS`.
- Principios: claridad, jerarquia visual fuerte, responsive, consistencia.

## Fase 0 - Base Tecnica
- Instalar y configurar Tailwind en Django (build local y produccion).
- Crear estructura de estilos base (`static/src/input.css`, `static/dist/output.css`).
- Definir layout base reutilizable (`templates/base.html`).
- Conectar tipografia, paleta y variables de diseno (tokens).

## Fase 1 - Sistema de Diseno
- Definir colores principales:
  - Primario (marca)
  - Secundario
  - Exito, alerta, error
  - Neutros (fondos, bordes, texto)
- Definir escala tipografica (h1-h6, cuerpo, labels, captions).
- Definir espaciados, radios, sombras y estados hover/focus.
- Crear componentes base:
  - Boton (`primario`, `secundario`, `peligro`)
  - Input/textarea/select
  - Card
  - Badge de estado (ACTIVE/USED/UNUSED, roles, etc.)
  - Alertas/mensajes (Correcto/Fallo/ya fue usado)

## Fase 2 - Estructura Global
- Crear navbar profesional con estado de sesion y accesos por rol.
- Mejorar footer simple y consistente.
- Unificar contenedores y max-width para escritorio/movil.
- Normalizar estilos de tablas administrativas.

## Fase 3 - Paginas Publicas (Usuario)
- Redisenar `home`:
  - Hero principal
  - Accesos claros por rol
  - Mensajes importantes visibles
- Redisenar `event_list`:
  - Cards de eventos, mejor lectura de fecha/lugar/categoria
  - CTA claro a detalle
- Redisenar `event_detail`:
  - Bloque de informacion del evento
  - Compra de boletas destacada
  - Seccion de opiniones visualmente limpia
- Redisenar `purchase_success` y `my_tickets`:
  - Jerarquia clara de informacion
  - Bloques de QR bien presentados

## Fase 4 - Modulos Admin/Operativos
- Redisenar pantallas admin internas:
  - Crear/editar/aprobar eventos
  - Gestion de usuarios
  - Gestion de boletas (filtros y tablas)
  - Validacion de token
  - Auditoria
- Mejorar visual de acciones criticas:
  - Botones de peligro (eliminar, quitar permisos)
  - Confirmaciones visuales
- Mejorar modal emergente de validacion (Correcto/Fallo/ya fue usado).

## Fase 5 - Perfil y Autenticacion
- Redisenar login/signup.
- Redisenar `mi perfil` (foto, nombre, bio, acciones).
- Mejorar estados de formulario (errores, focus, exito).

## Fase 6 - Responsive y UX
- Ajustar movil/tablet/escritorio en todas las vistas clave.
- Revisar densidad visual en tablas para pantallas pequenas.
- Mejorar navegacion tactil y tamano de botones.

## Fase 7 - Accesibilidad y Calidad
- Contraste de color AA en textos y botones.
- Focus visible en elementos interactivos.
- Etiquetas y estructura semantica correcta.
- Revision de consistencia visual final.

## Fase 8 - Cierre
- Limpiar estilos duplicados.
- Documentar decisiones visuales (mini guia en este archivo o `DESIGN_SYSTEM.md`).
- Checklist final de UI antes de demo.

## Orden de Implementacion Recomendado
1. Fase 0
2. Fase 1
3. Fase 2
4. Fase 3
5. Fase 4
6. Fase 5
7. Fase 6
8. Fase 7
9. Fase 8

## Criterios de Exito
- La interfaz se percibe profesional y moderna.
- Un usuario nuevo entiende el flujo principal en menos de 1 minuto.
- Los roles ven solo lo que necesitan, con UI clara.
- La app luce bien en movil y escritorio.

## Estado Actual
- Fase 0: `Completada` (base Tailwind por CDN + layout base).
- Fase 1: `Completada` (paleta, tipografia base y componentes principales aplicados en vistas).
- Fase 2: `Completada` (estructura global unificada en base y tablas admin).
- Fase 3: `Completada` (paginas principales de usuario estilizadas).
- Fase 4: `Completada` (modulos admin/operativos principales estilizados).
- Fase 5: `Completada` (login/signup/perfil redisenados).
- Fase 6: `Completada` (pulido responsive y consistencia fina aplicado).
- Fase 7: `Completada` (mejoras de accesibilidad aplicadas: foco visible, semantica y anuncios de estado).
- Fase 8: `Completada` (limpieza base de clases repetidas + guia `DESIGN_SYSTEM.md`).
- Cierre visual adicional: `Completado` (micro-ajustes de consistencia en botones, enlaces, paneles, copy y espaciado).
