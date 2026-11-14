"""Language handling for English / Hindi / Telugu.

Language is detected deterministically from the SCRIPT of the question (Telugu script -> te, Devanagari -> hi,
otherwise en). Latin-script "Hinglish" is treated as English on purpose. The LLM is only used to translate the
question into English (router call); everything fixed (refusals, errors, templates) is translated here.
"""
from __future__ import annotations

import re

SUPPORTED = ("en", "hi", "te")
LANG_NAME = {"en": "English", "hi": "Hindi (Devanagari script)", "te": "Telugu (Telugu script)"}

_TE = re.compile(r"[ఀ-౿]")
_HI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_language(text: str) -> str:
    te, hi, la = len(_TE.findall(text)), len(_HI.findall(text)), len(_LATIN.findall(text))
    if te and te >= hi and te * 2 >= la:  # Telugu script dominates (product names may be English)
        return "te"
    if hi and hi * 2 >= la:
        return "hi"
    return "en"


def has_script(text: str, lang: str, min_chars: int = 8) -> bool:
    """True if the text really contains the language's script (used to test that replies are localised)."""
    if lang == "te":
        return len(_TE.findall(text)) >= min_chars
    if lang == "hi":
        return len(_HI.findall(text)) >= min_chars
    return len(_LATIN.findall(text)) >= min_chars and not _TE.search(text) and not _HI.search(text)


M: dict[str, dict[str, str]] = {
    "not_found": {
        "en": "I couldn't find that in the shopkeeper guide. I can answer questions about your store's data (sales, stock, expiry, suppliers) and about what's in the uploaded shopkeeper documents.",
        "hi": "मुझे यह जानकारी दुकानदार गाइड में नहीं मिली। मैं आपकी दुकान के डेटा (बिक्री, स्टॉक, एक्सपायरी, सप्लायर) और अपलोड किए गए दस्तावेज़ों में मौजूद जानकारी के बारे में सवालों के जवाब दे सकता हूँ।",
        "te": "ఈ సమాచారం షాప్‌కీపర్ గైడ్‌లో నాకు కనిపించలేదు. మీ దుకాణం డేటా (అమ్మకాలు, స్టాక్, గడువు తేదీ, సరఫరాదారులు) గురించి మరియు అప్‌లోడ్ చేసిన డాక్యుమెంట్లలో ఉన్న సమాచారం గురించి నేను సమాధానం ఇవ్వగలను.",
    },
    "no_docs": {
        "en": "No shopkeeper documents have been uploaded yet, so I can't answer document questions. Upload a PDF, DOCX or TXT file first.",
        "hi": "अभी तक कोई दुकानदार दस्तावेज़ अपलोड नहीं किया गया है, इसलिए मैं दस्तावेज़ों से जुड़े सवालों का जवाब नहीं दे सकता। कृपया पहले PDF, DOCX या TXT फ़ाइल अपलोड करें।",
        "te": "ఇంకా ఏ షాప్‌కీపర్ డాక్యుమెంట్ అప్‌లోడ్ చేయలేదు, కాబట్టి డాక్యుమెంట్ ప్రశ్నలకు సమాధానం ఇవ్వలేను. ముందుగా PDF, DOCX లేదా TXT ఫైల్‌ను అప్‌లోడ్ చేయండి.",
    },
    "scope": {
        "en": "I can answer questions about your sales, revenue, profit, stock, reorder needs, expiry and suppliers.",
        "hi": "मैं आपकी बिक्री, राजस्व, मुनाफ़ा, स्टॉक, री-ऑर्डर की ज़रूरत, एक्सपायरी और सप्लायर के बारे में सवालों के जवाब दे सकता हूँ।",
        "te": "మీ అమ్మకాలు, ఆదాయం, లాభం, స్టాక్, రీఆర్డర్ అవసరాలు, గడువు తేదీలు మరియు సరఫరాదారుల గురించి నేను సమాధానం ఇవ్వగలను.",
    },
    "cannot_answer": {
        "en": "I can't answer that from the available data.",
        "hi": "मैं उपलब्ध डेटा से इसका जवाब नहीं दे सकता।",
        "te": "అందుబాటులో ఉన్న డేటా నుండి దీనికి నేను సమాధానం ఇవ్వలేను.",
    },
    "empty_q": {
        "en": "Please type a question.",
        "hi": "कृपया अपना सवाल लिखें।",
        "te": "దయచేసి మీ ప్రశ్న టైప్ చేయండి.",
    },
    "too_long": {
        "en": "That question is too long. Please keep it under 1000 characters.",
        "hi": "यह सवाल बहुत लंबा है। कृपया इसे 1000 अक्षरों से कम रखें।",
        "te": "ఈ ప్రశ్న చాలా పొడవుగా ఉంది. దయచేసి 1000 అక్షరాల లోపు ఉంచండి.",
    },
    "llm_down": {
        "en": "The AI service is temporarily unavailable. Please try again in a moment.",
        "hi": "AI सेवा अभी उपलब्ध नहीं है। कृपया थोड़ी देर बाद फिर कोशिश करें।",
        "te": "AI సేవ ప్రస్తుతం అందుబాటులో లేదు. దయచేసి కొద్దిసేపటి తర్వాత మళ్లీ ప్రయత్నించండి.",
    },
    "generic_fail": {
        "en": "Something went wrong while answering. Please try again.",
        "hi": "जवाब देते समय कुछ गड़बड़ हो गई। कृपया फिर से कोशिश करें।",
        "te": "సమాధానం ఇస్తున్నప్పుడు ఏదో తప్పు జరిగింది. దయచేసి మళ్లీ ప్రయత్నించండి.",
    },
    "bad_query": {
        "en": "I couldn't build a safe, valid query for that question. Please try rephrasing it.",
        "hi": "मैं इस सवाल के लिए सुरक्षित और सही क्वेरी नहीं बना सका। कृपया सवाल दूसरे शब्दों में पूछें।",
        "te": "ఈ ప్రశ్నకు సురక్షితమైన, సరైన క్వెరీని నేను రూపొందించలేకపోయాను. దయచేసి ప్రశ్నను వేరే విధంగా అడగండి.",
    },
    "no_records": {
        "en": "No matching records were found.",
        "hi": "कोई मिलता-जुलता रिकॉर्ड नहीं मिला।",
        "te": "సరిపోలే రికార్డులు ఏవీ కనిపించలేదు.",
    },
    "found_n": {
        "en": "I found {n} result(s){more}. The table below has the details.",
        "hi": "मुझे {n} परिणाम मिले{more}। विवरण नीचे तालिका में है।",
        "te": "{n} ఫలితాలు దొరికాయి{more}. వివరాలు కింద పట్టికలో ఉన్నాయి.",
    },
    "more_rows": {
        "en": " (showing the first rows only)",
        "hi": " (सिर्फ़ पहली पंक्तियाँ दिखाई गई हैं)",
        "te": " (మొదటి వరుసలు మాత్రమే చూపబడ్డాయి)",
    },
    "result": {"en": "Result", "hi": "परिणाम", "te": "ఫలితం"},
    "note_no_year": {
        "en": "No year was given, so the most recent matching period in the data ({label}) was used.",
        "hi": "वर्ष नहीं बताया गया, इसलिए डेटा में सबसे हाल की मिलती-जुलती अवधि ({label}) का उपयोग किया गया।",
        "te": "సంవత్సరం చెప్పలేదు, కాబట్టి డేటాలో ఇటీవలి సరిపోలే కాలం ({label}) ఉపయోగించబడింది.",
    },
}


def t(key: str, lang: str = "en", **kw) -> str:
    text = M[key].get(lang) or M[key]["en"]
    return text.format(**kw) if kw else text
