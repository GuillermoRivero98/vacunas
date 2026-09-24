# Glowiauy — tienda online gratis

3 archivos, sin build, sin frameworks:

- `index.html` — la tienda pública (catálogo).
- `admin.html` — panel privado para agregar/editar/borrar productos.
- `firebase-config.js` — conexión a Firebase, compartida por los dos anteriores.

Todo el "backend" es Firebase, plan **Spark (gratis, sin tarjeta)**.

---

## 1. Crear el proyecto Firebase

1. Andá a https://console.firebase.google.com → **Crear proyecto** → nombralo `glowiauy`.
2. Podés desactivar Google Analytics, no hace falta.
3. Dentro del proyecto, click en el ícono **`</>`** (agregar app web) → nombre "Glowiauy web" → **no** marques Firebase Hosting todavía (lo hacemos por CLI más abajo).
4. Te va a mostrar un bloque `firebaseConfig = {...}`. Copiá esos valores.

## 2. Completar `firebase-config.js`

Abrí `firebase-config.js` y reemplazá `TU_API_KEY`, `TU_SENDER_ID`, `TU_APP_ID` (y `authDomain`/`projectId`/`storageBucket` si no coinciden) con los valores reales que copiaste.

## 3. Activar Firestore (la base de datos)

1. En la consola de Firebase → **Firestore Database** → **Crear base de datos**.
2. Elegí **modo producción** y la región (cualquiera de Sudamérica está bien, o la que sugiera por defecto).
3. Andá a la pestaña **Reglas** y pegá esto (permite que cualquiera *lea* el catálogo, pero solo alguien logueado puede *escribir*):

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /productos/{producto} {
      allow read: if true;
      allow write: if request.auth != null;
    }
  }
}
```

4. **Publicar**.

## 4. Activar el login de la dueña

1. En la consola → **Authentication** → **Sbegin/Comenzar** → método **Email/contraseña** → activarlo.
2. Pestaña **Users** → **Add user** → cargá el mail y una contraseña para la dueña. Con esa cuenta va a entrar a `admin.html`.

No hace falta que ella sepa nada de Firebase: solo usa email + contraseña como en cualquier login.

## 5. Publicar el sitio (gratis) con Firebase Hosting

Necesitás Node instalado. Desde una terminal, parado en esta carpeta:

```bash
npm install -g firebase-tools
firebase login
firebase init hosting
```

Cuando pregunte:
- **"Use an existing project"** → elegí `glowiauy`.
- **"What do you want to use as your public directory?"** → escribí `.` (punto, la carpeta actual).
- **"Configure as a single-page app?"** → `No`.
- **"Set up automatic builds with GitHub?"** → `No` (a menos que lo quieras después).
- Si pregunta si sobreescribir `index.html`, decí **No**.

Después:

```bash
firebase deploy
```

Te va a dar una URL tipo `https://glowiauy.web.app` — esa es la tienda, ya online y gratis. Cada vez que quieras subir un cambio de diseño, repetís `firebase deploy`.

`admin.html` queda en la misma URL: `https://glowiauy.web.app/admin.html`. Esa es la que le pasás a ella (podés guardarla como acceso directo en su teléfono).

## 6. Cómo lo usa ella, día a día

1. Entra a `https://glowiauy.web.app/admin.html`.
2. Se loguea con su email y contraseña.
3. Agrega productos (nombre, precio, disponible/agotado) con el formulario de arriba.
4. Puede editar nombre/precio tocando el texto y apretando "Guardar", o tocar el botón de stock para alternar Disponible/Agotado — se refleja al instante en la tienda pública, sin que vos hagas nada.

## Costo real

- Firebase Hosting (Spark): gratis, 10 GB de transferencia/mes — de sobra para un catálogo chico.
- Firestore (Spark): gratis, ~50.000 lecturas/día — para el tráfico de una tienda que recién arranca, no lo va a rozar.
- Sin tarjeta de crédito cargada en ningún paso.

## Si en el futuro quiere crecer

- Fotos de producto → activar **Firebase Storage** (mismo plan gratis, con límite de 5 GB).
- Dominio propio (`glowiauy.com.uy`) → se conecta gratis desde Hosting, solo se paga el dominio en sí.
- Pagos online → recién ahí conviene evaluar Mercado Pago o similar, sin tocar el resto de la arquitectura.
