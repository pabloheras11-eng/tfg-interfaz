import streamlit as st
import json
import os
import re
import time
import unicodedata
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# 0. CONEXIÓN A FIREBASE
# ==========================================
# Verifica si la app ya está inicializada para evitar errores al recargar
if not firebase_admin._apps:
    # Lee las credenciales ocultas desde Streamlit Secrets
    cred_dict = dict(st.secrets["firebase"])
    cred = credentials.Certificate(cred_dict)
    try:
        firebase_admin.initialize_app(cred)
    except ValueError:
        # Otro hilo/sesión de Streamlit ya inicializó la app por medio (condición de carrera)
        pass

db = firestore.client()

# ==========================================
# 1. CARGA DE DATOS 
# ==========================================
@st.cache_data
def cargar_datos_unicos():
    with open("respuestas_alumnos_es_40.json", "r", encoding="utf-8") as f:
        datos = json.load(f)
    with open("reparto_grupos_40.json", "r", encoding="utf-8") as f:
        reparto = json.load(f)

    # hints_es_40.json es opcional: {question_id: hint_es}. Contiene el contexto/escena de la
    # pregunta (personajes, valores numéricos) para las preguntas donde el enunciado por sí solo
    # no se entiende sin él (p. ej. "Mackenzie" o "Quincy y Roger"). No se guardó en
    # respuestas_alumnos_es_40.json porque solo se usó como insumo interno al generar al alumno.
    hints = {}
    if os.path.exists("hints_es_40.json"):
        with open("hints_es_40.json", "r", encoding="utf-8") as f:
            hints = json.load(f)

    indice = {}
    for item in datos:
        if isinstance(item, list) and len(item) > 0: item = item[0]
        if not isinstance(item, dict) or "question_id" not in item: continue
        clave = (str(item["question_id"]), item.get("error_type"))
        indice[clave] = item

    resultado = {"A": [], "B": []}
    for entrada in reparto:
        clave = (str(entrada["question_id"]), entrada["error_type_asignado"])
        item = indice.get(clave)
        if item is not None:
            item["hint"] = hints.get(str(entrada["question_id"]), "")
            resultado[entrada["grupo"]].append(item)

    return resultado

datos_por_grupo = cargar_datos_unicos()

ROLES_HUMANOS = [
    "✅ Profesor Ideal (Misión: Corrige de forma precisa, bien desarrollada, clara y amable)",
    "❌ Profesor Desinformado (Misión: Da una respuesta incorrecta con toda la seguridad de que fuera correcta)",
    "✂️ Profesor Conciso (Misión: Da una corrección correcta y amable pero demasiado breve: te saltas pasos del razonamiento y das cosas por hechas, apenas explicas)",
    "🌀 Profesor Divagador (Misión: Da una corrección correcta y bien explicada, pero en algún momento te sales del tema para comentar detalles irrelevantes —aunque coherentes— de otra cosa)",
    "🤬 Profesor Tóxico (Misión: Da una corrección correcta, clara y bien explicada, pero con un tono brusco, irrespetuoso o incluso insultante)"
]

# Modo Profesor Voluntario, dos pasos:
#   Paso 1: el profesor escribe su propia pregunta y la explicación ideal — esa explicación SE
#   REUTILIZA directamente como la respuesta del rol Ideal, no se le vuelve a pedir en el Paso 2.
#   Paso 2: reformula esa misma corrección en los otros 4 roles de ROLES_VOLUNTARIO_PASO2 (todos
#   menos Ideal, ya cubierto por el Paso 1).
ROL_IDEAL = ROLES_HUMANOS[0]
ROLES_VOLUNTARIO_PASO2 = [r for r in ROLES_HUMANOS if r != ROL_IDEAL]

# ==========================================
# CÓDIGO CORTO DE ROL (para guardar en los JSON de salida en vez de la frase larga con emoji)
# ==========================================
CODIGO_ROL = {
    "✅ Profesor Ideal (Misión: Corrige de forma precisa, bien desarrollada, clara y amable)": "ideal",
    "❌ Profesor Desinformado (Misión: Da una respuesta incorrecta con toda la seguridad de que fuera correcta)": "desinformado",
    "✂️ Profesor Conciso (Misión: Da una corrección correcta y amable pero demasiado breve: te saltas pasos del razonamiento y das cosas por hechas, apenas explicas)": "conciso",
    "🌀 Profesor Divagador (Misión: Da una corrección correcta y bien explicada, pero en algún momento te sales del tema para comentar detalles irrelevantes —aunque coherentes— de otra cosa)": "divagador",
    "🤬 Profesor Tóxico (Misión: Da una corrección correcta, clara y bien explicada, pero con un tono brusco, irrespetuoso o incluso insultante)": "toxico",
}

def codigo_rol(rol):
    return CODIGO_ROL.get(rol, rol)

# ==========================================
# EQUIVALENCIA ENTRE ROLES HUMANOS Y RÚBRICA (R1-R4, rúbrica "real" definida por el tutor)
# ==========================================
# Vector de 4 booleanos (0/1), orden fijo:
#   R1 precisión factual   (identifica si la respuesta del alumno es correcta y, si no, por qué)
#   R2 profundidad          (la corrección está suficientemente desarrollada, no se salta pasos)
#   R3 claridad/concreción  (clara, se limita al tema, sin información irrelevante)
#   R4 amabilidad           (respetuosa, anima al alumno a mejorar)
# Indexado por el código corto de rol (el mismo que se guarda en "rol_profesor").
#
# *** POLARIDAD: 0 = BIEN (cumple el eje), 1 = MAL (falla ese eje) ***. Cada 1 marca el ÚNICO
# eje en el que ese rol falla a propósito, por eso "ideal" = (0,0,0,0).
#
# "desinformado" R2=0 (bien) es una decisión de diseño: R2 mide si se desarrolla el razonamiento
# que se presenta, no si es correcto (eso es R1). Una explicación puede ser profunda y falsa; si R2
# también penalizara el error, R1 y R2 medirían lo mismo. Ver resumen_avances_tfg.md, sección 12.
#
# respuestas_profesores_v3.py lee este diccionario directamente de este fichero (con ast) para
# generar el profesor sintético con la misma rúbrica: debe seguir siendo un literal (sin variables
# ni llamadas). respuestas_profesores_v2.py usa la rúbrica antigua y queda como histórico.
ROL_A_RUBRICA = {
    "ideal": (0, 0, 0, 0),
    "desinformado": (1, 0, 0, 0),
    "conciso": (0, 1, 0, 0),
    "divagador": (0, 0, 1, 0),
    "toxico": (0, 0, 0, 1),
}

# ==========================================
# GUÍAS DETALLADAS DE ROL (usadas tanto en el flujo normal de evaluación como en el modo voluntario)
# ==========================================
GUIA_DETALLADA_ROLES = {
    "✅ Profesor Ideal (Misión: Corrige de forma precisa, bien desarrollada, clara y amable)": """
**Misión:** Eres el profesor perfecto. Corrige de forma precisa, bien desarrollada, clara y amable.
* **Tono:** Empático, alentador y pedagógico (valida su intento y guíale usando ejemplos adaptados).
* **Ejemplo (Geografía):** *"¡Has estado muy cerca! Es normal confundirse porque ambas ciudades empiezan igual, pero la correcta es Madrid. ¿Recuerdas cuando la vimos en el mapa?"*
""",
    "❌ Profesor Desinformado (Misión: Da una respuesta incorrecta con toda la seguridad de que fuera correcta)": """
**Misión:** Da una respuesta incorrecta con toda la seguridad de que fuera correcta. Puede ser de tres formas: dile que ha acertado cuando en realidad se ha equivocado, dile que se ha equivocado cuando en realidad ha acertado, o dale el veredicto correcto pero con un motivo falso. El único fallo es de CONTENIDO — la explicación debe sonar igual de desarrollada, clara y amable que la de un buen profesor, solo que lo que dice es mentira.
* **Tono:** Igual de cordial y seguro que si tuvieras razón — el problema no es cómo lo dices, es que lo que dices es falso.
* **Ejemplo (Geografía):** *"¡Es Barcelona! Es la ciudad más grande de España, así que tiene que ser la capital."*
* **Si el alumno ya ha acertado:** No reconozcas el acierto — con la misma seguridad y amabilidad, dile que se ha equivocado, o confírmaselo pero con un motivo falso.
* **Ejemplo (acierto que niegas):** *"¡Casi! Pero la capital es Barcelona, no Madrid, porque es la ciudad más grande del país."*
""",
    "✂️ Profesor Conciso (Misión: Da una corrección correcta y amable pero demasiado breve: te saltas pasos del razonamiento y das cosas por hechas, apenas explicas)": """
**Misión:** Da una corrección correcta y amable, pero demasiado breve: te saltas pasos del razonamiento y das cosas por hechas, apenas explicas.
* **Tono:** Directo y muy breve, pero cordial — evita sonar frío o desagradable. Ve al grano con amabilidad.
* **Ejemplo (Geografía):** *"¡Es Madrid!"*
* **Si el alumno ya ha acertado:** Confírmalo igual de breve, pero con un toque amable.
* **Ejemplo (acierto):** *"¡Correcto!"*
""",
    "🌀 Profesor Divagador (Misión: Da una corrección correcta y bien explicada, pero en algún momento te sales del tema para comentar detalles irrelevantes —aunque coherentes— de otra cosa)": """
**Misión:** Da la respuesta correcta y una buena explicación (identificando el error del alumno), pero en algún momento te sales del tema — comentas datos u observaciones que encajan lógicamente con lo que estás explicando pero que ya no aportan nada para corregir al alumno.
* **Tono:** Cordial y bien intencionado — el problema no es el tono ni que el contenido principal esté mal, es que te dispersas y dejas de ir al grano.
* **Ejemplo (Geografía):** *"Es Madrid, no Barcelona. Por cierto, ¿sabías que Madrid es una de las capitales europeas a mayor altitud? Y hablando de altitud, la Sierra de Guadarrama que la rodea tiene picos de más de 2000 metros donde mucha gente esquía en invierno..."*
* **Si el alumno ya ha acertado:** Confírmalo con la explicación completa, pero divagando igual hacia detalles curiosos que no ha pedido.
* **Ejemplo (acierto):** *"¡Correcto, es Madrid! Aprovecho para contarte que su nombre viene del árabe 'Mayrit', que significa algo así como 'lugar de muchas aguas'... (y sigue con una curiosidad histórica que ya no ayuda con la pregunta)."*
""",
    "🤬 Profesor Tóxico (Misión: Da una corrección correcta, clara y bien explicada, pero con un tono brusco, irrespetuoso o incluso insultante)": """
**Misión:** Da la respuesta correcta y una buena explicación, completa y clara (identificando el error del alumno), pero dirígete al alumno de forma brusca, irrespetuosa o incluso insultante.
* **Tono:** Grosero, despectivo o insultante — va más allá de la simple impaciencia o el sarcasmo leve. El contenido es correcto y completo — el problema es CÓMO lo dices.
* **Ejemplo (Geografía):** *"¿En serio has confundido esto? Es Madrid, no Barcelona. ¿Es que no prestas atención en clase? Es de sentido común que ahí está el Gobierno."*
* **Si el alumno ya ha acertado:** Confírmalo con la explicación completa, con el mismo tono despectivo, como si no te esperaras que acertara.
* **Ejemplo (acierto):** *"Vaya, milagro. Sí, es Madrid, la capital, donde está el Gobierno. A ver si la próxima te la sabes sin tener que pensarlo tanto."*
""",
}

# ==========================================
# 2. FUNCIONES DE GESTIÓN (FIREBASE)
# ==========================================
def limpiar_nombre(nombre_crudo):
    nombre = nombre_crudo.strip().lower()
    nombre = ''.join(c for c in unicodedata.normalize('NFD', nombre) if unicodedata.category(c) != 'Mn')
    nombre = nombre.replace(" ", "_")
    return nombre

def parece_email(texto):
    return re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", texto.strip()) is not None

REGISTRO_ROLES_REF = db.collection("config").document("registro_roles")

# ==========================================
# ROTACIÓN DE ROLES POR BLOQUES (cuadrado latino)
# ==========================================
# Cada evaluador rota por los 5 roles en bloques de TAMANO_BLOQUE preguntas seguidas (4x5=20).
# Lo único que varía por evaluador es el DESPLAZAMIENTO (0-4), que decide con qué rol empieza:
#   rol_para_indice(indice, desplazamiento) = ROLES_ROTACION[(indice // 4 + desplazamiento) % 5]
# El orden de los roles es siempre el mismo (Ideal, Desinformado, Conciso, Divagador, Tóxico); el
# desplazamiento solo decide por cuál empieza cada evaluador, así el mismo bloque de preguntas le
# toca con un rol distinto a cada uno y cada evaluador pasa 4 preguntas por cada rol.
ROLES_ROTACION = ROLES_HUMANOS
TAMANO_BLOQUE = 20 // len(ROLES_ROTACION)  # 4, con los 5 roles actuales

def rol_para_indice(indice, desplazamiento):
    bloque = indice // TAMANO_BLOQUE
    return ROLES_ROTACION[(bloque + desplazamiento) % len(ROLES_ROTACION)]

# ==========================================
# BALANCEO DE DESPLAZAMIENTO + ID DE EVALUADOR
# ==========================================
# Estado global: un único documento Firestore (config/registro_roles) con la forma
# { id_limpio: {desplazamiento, id_numerico, grupo_preguntas} }, una clave por participante.
#
# id_limpio (limpiar_nombre) es la clave de deduplicación: si la persona vuelve a entrar,
# recupera el mismo desplazamiento e id (el progreso se reconstruye contando sus documentos
# en "evaluaciones"). LIMITACIÓN: dos personas con el mismo nombre normalizado colisionan; si
# alguien escribe su nombre distinto la 2ª vez, se le crea una identidad nueva.
#
# Todo ocurre dentro de una @firestore.transactional para que dos altas simultáneas no se
# pisen (dos Evaluador_NN iguales, o conteos obsoletos que rompan el balance).
#
# Reparto de desplazamiento = coge el menos usado hasta ahora (greedy, SIN random, para no
# desequilibrar el cuadrado latino con N pequeño). En empate, gana el de menor índice (dict
# con orden fijo 0-4), así que el desempate es determinista.
# ==========================================

@firestore.transactional
def _asignar_desplazamiento_y_id_tx(transaction, id_limpio):
    registro = REGISTRO_ROLES_REF.get(transaction=transaction).to_dict() or {}

    if id_limpio in registro:
        info = registro[id_limpio]
        if "desplazamiento" in info:
            return info["desplazamiento"], info["id_numerico"]

    numero_evaluador = len(registro) + 1
    id_numerico = f"Evaluador_{numero_evaluador:02d}"

    conteos_desplazamiento = {d: 0 for d in range(len(ROLES_ROTACION))}
    for info in registro.values():
        if isinstance(info, dict) and "desplazamiento" in info:
            conteos_desplazamiento[info["desplazamiento"]] += 1

    desplazamiento_elegido = min(conteos_desplazamiento, key=conteos_desplazamiento.get)

    registro[id_limpio] = {
        "desplazamiento": desplazamiento_elegido,
        "id_numerico": id_numerico
    }

    transaction.set(REGISTRO_ROLES_REF, registro)

    return desplazamiento_elegido, id_numerico

def asignar_desplazamiento_y_id(id_limpio):
    transaction = db.transaction()
    return _asignar_desplazamiento_y_id_tx(transaction, id_limpio)

@firestore.transactional
def _asignar_grupo_preguntas_tx(transaction, id_limpio):
    """Reparte equitativamente el Grupo A / Grupo B de preguntas entre participantes,
    con el mismo patrón de balanceo por conteo que asignar_desplazamiento_y_id, pero de UN SOLO
    nivel. Aquí los empates van SIEMPRE a "A" (conteos_grupo_preguntas es un dict
    literal, orden fijo), así que el reparto es determinista y alternante:
    1->A, 2->B, 3->A... Es independiente del rol (se puede ser Ideal+A o Ideal+B)."""
    registro = REGISTRO_ROLES_REF.get(transaction=transaction).to_dict() or {}

    info_existente = registro.get(id_limpio)
    if isinstance(info_existente, dict) and "grupo_preguntas" in info_existente:
        return info_existente["grupo_preguntas"]

    conteos_grupo_preguntas = {"A": 0, "B": 0}
    for info in registro.values():
        if isinstance(info, dict) and "grupo_preguntas" in info:
            conteos_grupo_preguntas[info["grupo_preguntas"]] += 1

    grupo_elegido = min(conteos_grupo_preguntas, key=conteos_grupo_preguntas.get)

    if not isinstance(registro.get(id_limpio), dict):
        registro[id_limpio] = {}
    registro[id_limpio]["grupo_preguntas"] = grupo_elegido

    transaction.set(REGISTRO_ROLES_REF, registro)

    return grupo_elegido

def asignar_grupo_preguntas(id_limpio):
    transaction = db.transaction()
    return _asignar_grupo_preguntas_tx(transaction, id_limpio)

def ya_registrado(id_limpio):
    """True si el correo ya tiene identidad asignada (la persona vuelve)."""
    return id_limpio in (REGISTRO_ROLES_REF.get().to_dict() or {})

# ==========================================
# 3. INICIALIZACIÓN DE VARIABLES DE SESIÓN
# ==========================================
if "empezado" not in st.session_state:
    st.session_state.empezado = False
if "indice" not in st.session_state:
    st.session_state.indice = 0
if "id_numerico" not in st.session_state:
    st.session_state.id_numerico = ""
if "id_evaluador_limpio" not in st.session_state:
    st.session_state.id_evaluador_limpio = ""
if "nombre_real" not in st.session_state:
    st.session_state.nombre_real = ""
if "desplazamiento_rol" not in st.session_state:
    st.session_state.desplazamiento_rol = None
if "grupo_preguntas" not in st.session_state:
    st.session_state.grupo_preguntas = ""
if "modo_voluntario" not in st.session_state:
    st.session_state.modo_voluntario = False
if "email_voluntario_persistente" not in st.session_state:
    st.session_state.email_voluntario_persistente = ""
if "preguntas_voluntario_completadas" not in st.session_state:
    st.session_state.preguntas_voluntario_completadas = 0
# Correo nuevo a la espera de que la persona confirme que está bien escrito.
if "correo_pendiente" not in st.session_state:
    st.session_state.correo_pendiente = ""
# Correo a precargar en el campo al pulsar "Corregir".
if "correo_borrador" not in st.session_state:
    st.session_state.correo_borrador = ""
if "aviso_bienvenida" not in st.session_state:
    st.session_state.aviso_bienvenida = ""

st.set_page_config(page_title="Simulador Docente", layout="wide")

# ==========================================
# 4. PANTALLA DE INICIO
# ==========================================
if st.session_state.modo_voluntario:
    st.title("👩‍🏫 Modo Profesor Voluntario - Universidad")
    st.markdown("""
    Gracias por colaborar. Aquí vas a **inventar tú mismo/a una pregunta** (con sus opciones y la
    explicación correcta, escrita como el mejor profesor posible) y luego vas a responderla **en otros
    4 estilos de profesor distintos**, escribiendo un feedback diferente para cada uno. Puedes repetir
    el proceso con tantas preguntas como quieras.
    """)
    st.write("---")

    if st.button("⬅️ Volver al inicio"):
        st.session_state.modo_voluntario = False
        st.session_state.email_voluntario_persistente = ""
        st.session_state.preguntas_voluntario_completadas = 0
        st.rerun()

    if st.session_state.email_voluntario_persistente == "":
        email_input_vol = st.text_input("Tu correo institucional:", placeholder="Ej: juan.perez@uam.es", key="email_voluntario_input")
        if st.button("Continuar", type="primary"):
            if email_input_vol.strip() == "":
                st.error("Por favor, introduce tu correo institucional.")
            elif not parece_email(email_input_vol):
                st.error("Ese correo no parece válido. Revisa que tenga el formato nombre@dominio.es")
            else:
                st.session_state.email_voluntario_persistente = email_input_vol.strip().lower()
                st.rerun()
    else:
        st.markdown(
            f"**Colaborador/a:** `{st.session_state.email_voluntario_persistente}` · "
            f"Preguntas enviadas esta sesión: **{st.session_state.preguntas_voluntario_completadas}**"
        )
        st.write("---")

        with st.form(key=f"form_voluntario_{st.session_state.preguntas_voluntario_completadas}"):
            st.markdown("### ✍️ Paso 1: Escribe tu propia pregunta")
            pregunta_texto = st.text_area("Enunciado de la pregunta:")
            op1 = st.text_input("Opción 1:")
            op2 = st.text_input("Opción 2:")
            op3 = st.text_input("Opción 3 (opcional):")
            op4 = st.text_input("Opción 4 (opcional):")
            correcta_num = st.radio("¿Cuál es la opción correcta?", [1, 2, 3, 4], index=None)
            explicacion_texto = st.text_area(
                "Explicación de por qué es correcta (escríbela ya como el mejor profesor posible: "
                "precisa, bien desarrollada, clara y amable — esta es directamente tu respuesta para el rol ✅ Ideal, "
                "no hace falta que la repitas después):"
            )

            st.write("---")
            st.markdown("### 📝 Paso 2: Responde en los otros 4 roles")
            st.caption("El rol ✅ Ideal ya queda cubierto con la explicación que has escrito arriba.")

            respuestas_por_rol_form = {}
            for rol in ROLES_VOLUNTARIO_PASO2:
                with st.container(border=True):
                    st.markdown(f"#### {rol}")
                    with st.expander("💡 Guía de actuación y ejemplos"):
                        st.markdown(GUIA_DETALLADA_ROLES.get(rol, ""))
                    texto_rol = st.text_area(
                        "Tu explicación al alumno, actuando como este rol:",
                        height=120,
                        key=f"texto_vol_{st.session_state.preguntas_voluntario_completadas}_{codigo_rol(rol)}"
                    )
                    respuestas_por_rol_form[rol] = texto_rol

            if st.form_submit_button("Guardar esta pregunta y sus 5 respuestas", type="primary"):
                opciones_vol = [op.strip() for op in [op1, op2, op3, op4] if op.strip() != ""]
                faltan_roles = [rol for rol, texto in respuestas_por_rol_form.items() if texto.strip() == ""]

                if pregunta_texto.strip() == "" or len(opciones_vol) < 2 or explicacion_texto.strip() == "":
                    st.error("Rellena al menos el enunciado, 2 opciones y la explicación.")
                elif correcta_num is None or correcta_num > len(opciones_vol):
                    st.error(f"Marca como correcta una opción que hayas rellenado (tienes {len(opciones_vol)}).")
                elif faltan_roles:
                    st.error("Falta completar la explicación en: " + ", ".join(faltan_roles))
                else:
                    respuestas_por_rol_final = {
                        codigo_rol(ROL_IDEAL): {"explanation": explicacion_texto.strip()}
                    }
                    respuestas_por_rol_final.update({
                        codigo_rol(rol): {"explanation": texto.strip()}
                        for rol, texto in respuestas_por_rol_form.items()
                    })

                    nuevo_registro = {
                        "evaluador": {"email": st.session_state.email_voluntario_persistente},
                        "pregunta": pregunta_texto.strip(),
                        "opciones": opciones_vol,
                        "respuesta_correcta_index": correcta_num - 1,
                        "explicacion": explicacion_texto.strip(),
                        "respuestas_por_rol": respuestas_por_rol_final
                    }

                    try:
                        db.collection("evaluaciones_voluntarios_uni").add(nuevo_registro)
                    except Exception as e:
                        st.error(f"No se pudo guardar tu pregunta, inténtalo de nuevo. (Error: {e})")
                    else:
                        st.session_state.preguntas_voluntario_completadas += 1
                        st.rerun()

elif not st.session_state.empezado:
    st.title("🔬 Simulador de Interacción Docente - TFG")

    st.markdown("""
    ### ¡Hola! 👋 Bienvenido al simulador.
    Gracias por participar en este experimento para mi TFG. Tu ayuda es clave para entrenar a una futura Inteligencia Artificial educativa.
    
    #### 🎮 ¿Cómo funciona esto?
    Te vamos a poner en la piel de un profesor. Durante **20 preguntas** verás respuestas de alumnos simulados, organizadas en dos bloques:
    
    * 📘 **A la izquierda (La Referencia):** Verás la pregunta original y la "Solución de libro". Es tu chuleta para saber cuál es la respuesta correcta y por qué.
    * 🧑‍🎓 **A la derecha (El Alumno):** Verás lo que ha contestado el estudiante y cómo lo justifica (a veces aciertan, y a veces se equivocan o dudan).
    
    #### 🎯 Tu misión y Reglas de Oro
    El sistema te irá asignando distintas "personalidades" o estilos docentes a lo largo de las 20 preguntas — **tu rol cambia cada 4 preguntas**, así que pasarás por varios estilos distintos. **Tu objetivo en cada pregunta es corregir al alumno escribiendo un *feedback* actuando exactamente como te pide el rol de ese momento** (lo verás indicado en cada pregunta, en el recuadro amarillo que hay debajo de la respuesta del alumno).

    ⚠️ **Por favor, ten muy en cuenta estas indicaciones:**
    1. **Usa la ayuda solo si es vital:** A veces aparecerá un botón de "Ayuda Pedagógica". Intenta deducir el error del alumno por ti mismo leyendo su justificación, y pulsa el botón de ayuda **solo si es estrictamente necesario** o te quedas atascado.
    2. **Si no tienes claro cómo actuar en tu rol:** junto a cada rol tienes una chuleta (💡 "RECUERDA CÓMO DEBES ACTUAR") con un ejemplo. Ese ejemplo es siempre sobre una pregunta inventada de geografía (la capital de España) — no tiene nada que ver con tu pregunta real de ciencias, es solo para que veas el ESTILO del rol (el tono, si debe ser breve o desarrollado...).
    """)
    st.write("---")
    
    st.subheader("👤 Registro de Participante")
    # Se identifica por correo (no por nombre): es único y se escribe siempre igual al volver a
    # entrar. Se sigue guardando en evaluador.nombre / id_limpio para no cambiar el esquema.
    # Un correo NUEVO se confirma antes de registrarlo: si alguien que ya había empezado lo
    # escribe distinto al volver, se le avisa en vez de crearle otra identidad sin que lo note.
    entrar = False
    vuelve = False
    if st.session_state.correo_pendiente:
        nombre_input = st.session_state.correo_pendiente
        st.warning(f"Es la primera vez que entras con **{nombre_input}**. ¿Está bien escrito?\n\n"
                   "⚠️ Si ya habías empezado antes, pulsa «Corregir» y revisa el correo: con uno distinto "
                   "empezarías de cero.")
        col_si, col_no = st.columns(2)
        if col_si.button("✅ Sí, es correcto: empezar", type="primary"):
            st.session_state.correo_pendiente = ""
            entrar = True
        if col_no.button("✏️ Corregir el correo"):
            st.session_state.correo_borrador = nombre_input
            st.session_state.correo_pendiente = ""
            st.rerun()
    else:
        nombre_input = st.text_input("Por favor, escribe tu correo electrónico:", value=st.session_state.correo_borrador,
                                     placeholder="Ej: nombre@gmail.com")
        st.caption("🔒 Tu correo es confidencial: solo se usará para identificarte si retomas el experimento otro día "
                   "y para enviarte un recordatorio si no lo has terminado. En el análisis de los datos apareces "
                   "con un identificador anónimo. Usa siempre el mismo correo para continuar donde lo dejaste.")

        if st.button("🚀 Empezar Experimento", type="primary"):
            if nombre_input.strip() == "":
                st.error("Por favor, introduce tu correo para continuar.")
            elif not parece_email(nombre_input):
                st.error("Ese correo no parece válido. Revisa que tenga el formato nombre@dominio.es")
            elif ya_registrado(limpiar_nombre(nombre_input)):
                entrar = True
                vuelve = True
            else:
                st.session_state.correo_pendiente = nombre_input.strip()
                st.rerun()

    if entrar:
        id_limpio = limpiar_nombre(nombre_input)
        desplazamiento, id_num = asignar_desplazamiento_y_id(id_limpio)
        grupo_preguntas = asignar_grupo_preguntas(id_limpio)

        evaluaciones_previas = db.collection("evaluaciones").where("evaluador.id_limpio", "==", id_limpio).get()
        # Preguntas distintas, no registros: si una respuesta se hubiera guardado dos veces
        # (p. ej. reintento tras un fallo de red), contar registros haría saltarse una pregunta.
        st.session_state.indice = len({d.to_dict()["question_id"] for d in evaluaciones_previas})

        if len(evaluaciones_previas) > 0:
            st.session_state.nombre_real = evaluaciones_previas[0].to_dict()["evaluador"].get("nombre", nombre_input.strip())
        else:
            st.session_state.nombre_real = nombre_input.strip()

        if vuelve:
            total = len(datos_por_grupo.get(grupo_preguntas, []))
            st.session_state.aviso_bienvenida = (f"👋 ¡Bienvenido/a de nuevo! Llevas {st.session_state.indice} de "
                                                 f"{total} preguntas: sigues donde lo dejaste.")

        st.session_state.id_evaluador_limpio = id_limpio
        st.session_state.id_numerico = id_num
        st.session_state.desplazamiento_rol = desplazamiento
        st.session_state.grupo_preguntas = grupo_preguntas
        st.session_state.correo_borrador = ""
        st.session_state.empezado = True
        st.rerun()

    st.write("---")
    st.markdown("#### 👩‍🏫 ¿Eres profesor/a voluntario/a de la Universidad?")
    st.caption("Si te has ofrecido a colaborar inventando preguntas y respondiéndolas en varios estilos de profesor, entra aquí.")
    if st.button("👉 Acceder al modo Profesor Voluntario"):
        st.session_state.modo_voluntario = True
        st.rerun()

# ==========================================
# 5. PANTALLA PRINCIPAL
# ==========================================
else:
    datos_alumnos = datos_por_grupo.get(st.session_state.grupo_preguntas, [])

    if st.session_state.indice < len(datos_alumnos):

        caso_actual = datos_alumnos[st.session_state.indice]
        rol_actual = rol_para_indice(st.session_state.indice, st.session_state.desplazamiento_rol)
        resp_estudiante = caso_actual['student_response']
        opciones = caso_actual['choices']

        tracker_key = f"ayuda_historial_{st.session_state.indice}"
        if tracker_key not in st.session_state:
            st.session_state[tracker_key] = False

        # Hora en que se mostró la pregunta (los reruns, p. ej. al activar la ayuda, no la reinician).
        inicio_key = f"inicio_pregunta_{st.session_state.indice}"
        if inicio_key not in st.session_state:
            st.session_state[inicio_key] = time.time()

        if st.session_state.aviso_bienvenida:
            st.success(st.session_state.aviso_bienvenida)
            st.session_state.aviso_bienvenida = ""

        st.progress(st.session_state.indice / len(datos_alumnos))

        st.markdown(f"**{st.session_state.id_numerico}** | Usuario: `{st.session_state.nombre_real}` | Grupo: **{st.session_state.grupo_preguntas}**")
        st.subheader(f"Pregunta {st.session_state.indice + 1} de {len(datos_alumnos)}")
        
        col_izq, col_der = st.columns([1.1, 1], gap="large")

        with col_izq:
            texto_grado_pregunta = f"**Grado del alumno:** {caso_actual.get('grade', 'Desconocido')}"
            if caso_actual.get('hint'):
                texto_grado_pregunta += f"\n\n**Contexto:** {caso_actual['hint']}"
            texto_grado_pregunta += f"\n\n**Pregunta:** {caso_actual['question']}"
            st.info(texto_grado_pregunta)
            st.markdown("**Opciones:**\n" + "\n".join([f"- {opt}" for opt in opciones]))
            
            st.markdown("### 📘 Solución de libro")
            st.caption("*(Esta es la explicación 'de libro'. Úsala como tu chuleta personal para entender el problema, pero **no se la copies y pegues al alumno**, adáptala según tu rol asignado)*.")
            
            idx_corr = caso_actual['ground_truth_answer']
            st.success(f"**Opción Correcta Real:** {opciones[idx_corr]}\n\n**Explicación Oficial:** {caso_actual['ground_truth_solution']}")

        with col_der:
            st.markdown("### 🧑‍🎓 Respuesta del Alumno")
            st.caption("*(Esto es lo que ha contestado el estudiante basándose en sus conocimientos. Lee su justificación para darle un feedback adecuado a su razonamiento)*.")
            
            if caso_actual['error_type'] != "None":
                mostrar_ayuda = st.toggle("🔍 Ayuda Pedagógica (Úsala solo si es necesario)", key=f"toggle_{st.session_state.indice}")
                if mostrar_ayuda:
                    st.session_state[tracker_key] = True
            else:
                mostrar_ayuda = False
            
            solucion = resp_estudiante['solution']
            extracto = resp_estudiante.get('error_excerpt')
            
            if mostrar_ayuda and extracto and extracto != "null":
                resaltado = f"<span style='color: #d32f2f; font-weight: bold; background-color: #ffebee; padding: 0 4px; border-radius: 3px;'>{extracto}</span>"
                solucion = solucion.replace(extracto, resaltado)

            with st.container(border=True):
                if caso_actual['error_type'] == "None":
                    st.markdown(f"✅ **El alumno eligió la CORRECTA:** \n{opciones[resp_estudiante['answer']]}")
                else:
                    st.markdown(f"❌ **El alumno eligió una INCORRECTA:** {opciones[resp_estudiante['answer']]}")
                st.markdown(f"**Justificación del alumno:**\n\n{solucion}", unsafe_allow_html=True)
            
            if mostrar_ayuda and caso_actual['error_type'] != "None":
                st.warning(f"💡 **Explicación técnica del error:**\n\n{resp_estudiante.get('error_explanation')}")

        st.write("---")
        st.markdown("### 📝 Tu Evaluación")

        # Nombre del rol en grande y la misión debajo; en la 1ª pregunta de cada bloque (salvo la
        # primera de todas) se avisa además de que el rol ha cambiado.
        nombre_rol, mision_rol = rol_actual.split(" (Misión: ", 1)
        aviso_cambio = ""
        if st.session_state.indice > 0 and st.session_state.indice % TAMANO_BLOQUE == 0:
            aviso_cambio = "🔄 **¡CAMBIO DE ROL!** A partir de esta pregunta tienes un rol nuevo.\n\n"
        st.warning(f"{aviso_cambio}🎭 **TU ROL PARA ESTE BLOQUE DE PREGUNTAS:**\n\n### {nombre_rol}\n\n**Misión:** {mision_rol.rstrip(')')}")
            
        with st.expander("💡 RECUERDA CÓMO DEBES ACTUAR (Ver guías y ejemplos)"):
            st.markdown(GUIA_DETALLADA_ROLES.get(rol_actual, ""))
        
        with st.form(key=f"form_{st.session_state.indice}"):
            opcion_humano = st.radio("¿Qué opción le indicarás al alumno como la correcta?", opciones, index=None)
            
            # índice 2, no 1: el formato es "{emoji} Profesor {Palabra} (...)" -> [0]=emoji,
            # [1]="Profesor", [2]=nombre real del rol.
            nombre_rol_corto = rol_actual.split(' ')[2]
            respuesta_humano = st.text_area(f"✍️ Redacta tu feedback para el alumno actuando como el {nombre_rol_corto}:", height=150, placeholder="Escribe aquí tu justificación y feedback...")
            
            if st.form_submit_button("Guardar y Siguiente", type="primary"):
                if opcion_humano is None or respuesta_humano.strip() == "":
                    st.error("Por favor, selecciona una opción y escribe una respuesta para el alumno.")
                else:
                    codigo_rol_actual = codigo_rol(rol_actual)
                    nuevo_registro = {
                        "evaluador": {
                            "id": st.session_state.id_numerico,
                            "id_limpio": st.session_state.id_evaluador_limpio,
                            "nombre": st.session_state.nombre_real
                        },
                        "question_id": caso_actual['question_id'],
                        "question": caso_actual['question'],
                        "choices": opciones,
                        "ground_truth": {
                            "answer": caso_actual['ground_truth_answer'],
                            "solution": caso_actual['ground_truth_solution']
                        },
                        "error_type": caso_actual['error_type'],
                        "student_response": resp_estudiante,
                        "rol_profesor": codigo_rol_actual,
                        # Rúbrica objetivo (R1-R4) congelada en el momento de la captura: si
                        # ROL_A_RUBRICA se redefine más adelante, este registro conserva la que
                        # estaba vigente cuando se escribió, en vez de reinterpretarse con la nueva.
                        "rubrica_objetivo": ROL_A_RUBRICA.get(codigo_rol_actual),
                        "grupo_preguntas": st.session_state.grupo_preguntas,
                        "human_response": {
                            "selected_choice_text": opcion_humano,
                            "selected_choice_index": opciones.index(opcion_humano),
                            "explanation": respuesta_humano,
                            "ayuda_pedagogica_utilizada": st.session_state[tracker_key],
                            "segundos_en_pregunta": round(time.time() - st.session_state[inicio_key], 1)
                        }
                    }
                    
                    try:
                        db.collection("evaluaciones").add(nuevo_registro)
                    except Exception as e:
                        st.error(f"No se pudo guardar tu respuesta, inténtalo de nuevo. (Error: {e})")
                    else:
                        st.session_state.indice += 1
                        st.rerun()

    else:
        st.success("¡Has completado las 20 evaluaciones! Muchísimas gracias por tu tiempo.")
        st.balloons()
        if st.button("🔄 Volver al inicio", type="primary"):
            st.session_state.empezado = False
            st.session_state.indice = 0
            st.session_state.id_evaluador_limpio = ""
            st.session_state.id_numerico = ""
            st.session_state.nombre_real = ""
            st.session_state.desplazamiento_rol = None
            st.session_state.grupo_preguntas = ""
            st.rerun()
