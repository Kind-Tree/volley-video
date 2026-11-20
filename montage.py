#!/usr/bin/env python3
"""montage.py : montages d'actions de volley (Python + ffmpeg).

Exemples :
    python montage.py paris                 # montage de toutes les actions du match
    python montage.py paris --tag attaque   # seulement les actions taguees "attaque"
    python montage.py --tous --tag reception
    python montage.py paris --precis | --incruster | --partage | --separes
    python montage.py paris --verifier      # controle les timecodes sans rien produire

Dossiers : matchs/ (videos brutes, jamais modifiees), timecodes/ (un .csv par
match, meme nom que la video), montages/ (resultats).

Format de timecodes/<match>.csv (une action par ligne) :
    1:10 -> Trou au bloc a couvrir #bloc   <- un temps = debut du point : clip de 1 s avant a 9 s apres
    1:10 +15 -> texte                      <- 15 s apres le temps (au lieu de 9)
    1:10-1:25 -> texte                     <- debut et fin explicites
    4m58 -> texte                          <- 4m58 = 4:58
    12:35,12:48,libelle,tag1;tag2          <- ancien format CSV, toujours lu
    @1:10,Texte precis                     <- commentaire a un instant precis (6 en dernier champ = 6 s)
    # ligne ignoree
Le texte apres -> s'affiche pendant tout le clip. Les #mots sont des tags (pour --tag), retires du texte.
Chaque clip recoit un chapitre : « numero - temps d'origine - commentaire ».
"""
import argparse
import csv
import math
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field

RACINE = os.path.dirname(os.path.abspath(__file__))
DOSSIER_MATCHS = os.path.join(RACINE, "matchs")
DOSSIER_TIMECODES = os.path.join(RACINE, "timecodes")
DOSSIER_MONTAGES = os.path.join(RACINE, "montages")
EXTENSIONS = (".mp4", ".mov", ".mkv", ".m4v")

DUREE_APRES_DEFAUT = 9.0       # un seul temps : le clip dure 9 s apres ce temps (--duree)
MARGE_AVANT_DEFAUT = 1.0       # et commence 1 s avant (--marge)
MARGE_APRES_DEFAUT = 0.0       # marge apres la fin pour les plages debut-fin (--marge avant apres)
DUREE_DEFAUT_COMMENTAIRE = 4.0 # commentaires @ : duree d'affichage
MARGES = (MARGE_AVANT_DEFAUT, MARGE_APRES_DEFAUT)  # (avant, apres), fixees par main()
CHAPITRE_DECALAGE_MAX = 0.05   # tolerance du controle des chapitres (s)

for _flux in (sys.stdout, sys.stderr):
    try:
        _flux.reconfigure(errors="replace")
    except Exception:
        pass


class Erreur(Exception):
    """Erreur a afficher telle quelle a l'utilisateur."""


# --------------------------------------------------------------------------
# Outils ffmpeg
# --------------------------------------------------------------------------
def trouver_outil(nom):
    chemin = shutil.which(nom)
    if chemin:
        return chemin
    if os.name == "nt":  # installation winget pas encore dans le PATH de cette session
        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
        trouves = glob.glob(os.path.join(base, "Gyan.FFmpeg*", "**", "bin", nom + ".exe"), recursive=True)
        if trouves:
            return trouves[0]
    raise Erreur(
        f"{nom} est introuvable. Installation sous Windows : "
        "winget install Gyan.FFmpeg, puis ouvrir un nouveau terminal."
    )


FFMPEG = FFPROBE = None


def init_outils():
    global FFMPEG, FFPROBE
    FFMPEG = trouver_outil("ffmpeg")
    FFPROBE = trouver_outil("ffprobe")


def lancer(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        fin = "\n".join(r.stderr.strip().splitlines()[-12:])
        raise Erreur(f"ffmpeg a echoue ({os.path.basename(cmd[0])}) :\n{fin}")
    return r.stdout


def ffmpeg(*args, cwd=None):
    lancer([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], cwd=cwd)


def sonder(chemin):
    """Infos utiles d'une video : duree, debut du flux, formats video/audio."""
    out = lancer([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", chemin])
    j = json.loads(out)
    fmt = j.get("format", {})
    video = next((s for s in j["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    if video is None:
        raise Erreur(f"Pas de piste video dans {chemin}")
    rotation = 0
    try:
        rotation = int(video.get("tags", {}).get("rotate", 0))
    except ValueError:
        pass
    for sd in video.get("side_data_list", []):
        if "rotation" in sd:
            rotation = int(float(sd["rotation"]))
    return {
        "duree": float(fmt.get("duration", 0)),
        "debut": float(fmt.get("start_time", 0) or 0),
        "format": (
            video["codec_name"], video.get("width"), video.get("height"), video.get("pix_fmt"),
            video.get("r_frame_rate"), rotation,
            audio["codec_name"] if audio else None,
            audio.get("sample_rate") if audio else None,
            audio.get("channels") if audio else None,
        ),
        "largeur": video.get("width"), "hauteur": video.get("height"),
        "rotation": rotation, "audio": audio is not None,
    }


def decrire_format(f):
    return (f"{f[0]} {f[1]}x{f[2]} {f[3]} {f[4]} i/s rotation {f[5]}, "
            f"audio {f[6]} {f[7]} Hz {f[8]} can.")


def duree_fichier(chemin):
    out = lancer([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", chemin])
    return float(out.strip())


def images_cles(chemin, debut_flux):
    """Instants (en temps du lecteur, donc depuis 0) des images cles de la video."""
    out = lancer([FFPROBE, "-v", "error", "-select_streams", "v:0",
                  "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", chemin])
    cles = []
    for ligne in out.splitlines():
        p = ligne.split(",")
        if len(p) >= 2 and "K" in p[1] and p[0] not in ("", "N/A"):
            cles.append(float(p[0]) - debut_flux)
    cles.sort()
    return cles


# --------------------------------------------------------------------------
# Lecture des timecodes
# --------------------------------------------------------------------------
def sans_accents(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def slug(s):
    s = sans_accents(s).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "x"


def lire_temps(texte):
    """mm:ss, h:mm:ss ou secondes (decimales acceptees) -> secondes."""
    t = texte.strip().replace(",", ".")
    m = re.fullmatch(r"(\d+)m(\d{1,2})", t)  # forme 4m58
    if m:
        t = f"{m.group(1)}:{m.group(2)}"
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", t):
        raise ValueError(f"temps illisible : « {texte.strip()} »")
    parts = t.split(":")
    if len(parts) > 1 and any(float(p) >= 60 for p in parts[1:]):
        raise ValueError(f"minutes ou secondes >= 60 : « {texte.strip()} »")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def nettoyer_texte(texte):
    """Texte de commentaire sans caracteres qui ont un sens pour les sous-titres ou les metadonnees :
    antislash devient « / », accolades deviennent des parentheses."""
    return texte.replace("\\", "/").replace("{", "(").replace("}", ")").strip()


def fmt_temps(s):
    s = max(0, s)
    h, reste = divmod(int(s), 3600)
    m, sec = divmod(reste, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


@dataclass
class Action:
    match: str
    debut: float
    fin: float
    libelle: str = ""
    tags: tuple = ()
    ligne: int = 0
    commentaire: str = ""   # texte affiche pendant tout le clip (syntaxe « temps -> texte »)
    explicite: bool = False # True si la fin a ete donnee (plage debut-fin) : la marge apres s'applique
    temps: float = None     # temps d'origine annonce dans le chapitre (le plus ancien si clips fusionnes)
    fusion: tuple = ()      # lignes des actions fusionnees dans celle-ci


@dataclass
class Commentaire:
    match: str
    t: float
    texte: str
    duree: float = None  # None = defaut
    ligne: int = 0
    affiche: bool = False
    motif: str = ""  # pourquoi il n'est pas affiche


@dataclass
class Clip:
    action: Action
    debut: float          # debut demande (marge comprise)
    fin: float
    origine: float = 0.0  # instant source ou le clip commence reellement
    duree: float = 0.0    # duree reelle du clip
    decalage: float = 0.0 # position du clip dans le montage
    fichier: str = ""


_TEMPS = r"\d+m\d{1,2}|\d+(?::\d{1,2}){0,2}(?:\.\d+)?"
LIGNE_ACTION = re.compile(
    rf"^({_TEMPS})(?:\s*-\s*({_TEMPS})|\s*\+\s*(\d+(?:[.,]\d+)?))?\s*(?:(?:->|→)\s*(.*))?$")


def lire_fichier_timecodes(chemin, match, duree_video, problemes, duree_apres=DUREE_APRES_DEFAUT):
    """Retourne (actions, commentaires). Les lignes incoherentes vont dans `problemes`."""
    actions, commentaires = [], []
    with open(chemin, encoding="utf-8-sig") as f:
        lignes = f.read().splitlines()
    for n, brut in enumerate(lignes, 1):
        ligne = brut.strip()
        if not ligne or ligne.startswith("#"):
            continue
        ou = f"{match} ligne {n}"
        try:
            if ligne.startswith("@"):
                champs = next(csv.reader([ligne[1:]], skipinitialspace=False))
                t = lire_temps(champs[0])
                duree = None
                reste = champs[1:]
                if len(reste) >= 2 and re.fullmatch(r"\s*\d+([.,]\d+)?\s*", reste[-1]):
                    duree = float(reste[-1].strip().replace(",", "."))
                    reste = reste[:-1]
                texte = nettoyer_texte(",".join(reste))
                if not texte:
                    raise ValueError("commentaire vide")
                if duree is not None and duree <= 0:
                    raise ValueError("duree d'affichage <= 0")
                if duree_video and t > duree_video:
                    raise ValueError(f"commentaire a {fmt_temps(t)}, apres la fin de la video ({fmt_temps(duree_video)})")
                commentaires.append(Commentaire(match, t, texte, duree, n))
                continue
            m = LIGNE_ACTION.match(ligne)
            if m:  # syntaxe « temps [-fin | +duree] [-> texte #tags] »
                debut = lire_temps(m.group(1))
                explicite = False
                if m.group(2):
                    fin, explicite = lire_temps(m.group(2)), True
                elif m.group(3):
                    apres = float(m.group(3).replace(",", "."))
                    if apres <= 0:
                        raise ValueError("duree apres le temps <= 0")
                    fin = debut + apres
                else:
                    fin = debut + duree_apres
                texte = m.group(4) or ""
                tags = tuple(t.lower() for t in re.findall(r"#([\w-]+)", texte))
                for t in tags:
                    if t.isdigit():
                        problemes.append(f"{ou} : « #{t} » est lu comme un tag et retire du texte affiche.")
                texte = nettoyer_texte(re.sub(r"\s*#[\w-]+", "", texte))
                if fin <= debut:
                    raise ValueError(f"fin ({fmt_temps(fin)}) avant ou egale au debut ({fmt_temps(debut)})")
                if duree_video and debut >= duree_video:
                    raise ValueError(f"debut {fmt_temps(debut)} au-dela de la duree de la video ({fmt_temps(duree_video)})")
                if duree_video and fin > duree_video:
                    if explicite:
                        problemes.append(f"{ou} : fin {fmt_temps(fin)} au-dela de la video ({fmt_temps(duree_video)}), ramenee a la fin de la video.")
                    fin = duree_video
                actions.append(Action(match, debut, fin, texte, tags, n, texte, explicite, debut))
                continue
            brutes = next(csv.reader([ligne]))
            champs = [c.strip() for c in brutes]
            if n == 1 or not actions and not commentaires:
                if champs[0].lower().replace("é", "e") in ("debut", "début"):
                    continue
            debut = lire_temps(champs[0])
            fin_txt = champs[1] if len(champs) > 1 else ""
            fin = lire_temps(fin_txt) if fin_txt else debut + duree_apres
            if len(champs) > 4:  # virgules dans le libelle
                libelle = ",".join(brutes[2:-1]).strip()
                tags_txt = champs[-1]
            else:
                libelle = champs[2] if len(champs) > 2 else ""
                tags_txt = champs[3] if len(champs) > 3 else ""
            tags = tuple(t.strip().lower() for t in tags_txt.split(";") if t.strip())
            if fin <= debut:
                raise ValueError(f"fin ({fmt_temps(fin)}) avant ou egale au debut ({fmt_temps(debut)})")
            if duree_video and debut >= duree_video:
                raise ValueError(f"debut {fmt_temps(debut)} au-dela de la duree de la video ({fmt_temps(duree_video)})")
            if duree_video and fin > duree_video:
                problemes.append(f"{ou} : fin {fmt_temps(fin)} au-dela de la video ({fmt_temps(duree_video)}), ramenee a la fin de la video.")
                fin = duree_video
            actions.append(Action(match, debut, fin, libelle, tags, n, "", bool(fin_txt), debut))
        except ValueError as e:
            problemes.append(f"{ou} IGNOREE : {e}   [{brut.strip()}]")
    return actions, commentaires


# --------------------------------------------------------------------------
# Choix des matchs
# --------------------------------------------------------------------------
def videos_disponibles():
    if not os.path.isdir(DOSSIER_MATCHS):
        return []
    return sorted(f for f in os.listdir(DOSSIER_MATCHS) if f.lower().endswith(EXTENSIONS))


def trouver_match(motif):
    vids = videos_disponibles()
    stems = {os.path.splitext(v)[0]: v for v in vids}
    if motif in stems:
        return motif
    if motif in vids:
        return os.path.splitext(motif)[0]
    cible = slug(os.path.splitext(motif)[0])
    cands = [s for s in stems if cible in slug(s)]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        raise Erreur(f"Aucune video ne correspond a « {motif} » dans matchs/. Videos : "
                     + (", ".join(stems) or "(aucune)"))
    raise Erreur(f"« {motif} » correspond a plusieurs videos : {', '.join(cands)}. Precise la date ou le nom.")


def chemin_video(match):
    for ext in EXTENSIONS:
        for variante in (ext, ext.upper()):
            p = os.path.join(DOSSIER_MATCHS, match + variante)
            if os.path.isfile(p):
                return p
    raise Erreur(f"Video introuvable pour {match} dans matchs/.")


def chemin_timecodes(match):
    return os.path.join(DOSSIER_TIMECODES, match + ".csv")


# --------------------------------------------------------------------------
# Noms de sortie
# --------------------------------------------------------------------------
def nom_libre(dossier, base, extensions):
    """base, base_2, base_3... jusqu'a ce qu'aucun fichier du lot n'existe."""
    n = 1
    while True:
        nom = base if n == 1 else f"{base}_{n}"
        if not any(os.path.exists(os.path.join(dossier, nom + e)) for e in extensions):
            return nom
        n += 1


# --------------------------------------------------------------------------
# Commentaires -> sous-titres
# --------------------------------------------------------------------------
def placer_commentaires(clips, commentaires, toutes_actions, signal):
    """Retourne la liste des (debut, fin, texte) dans le temps du montage :
    le commentaire de chaque clip (pendant tout le clip) puis les commentaires @ (instant precis)."""
    cues_clips = [(k.decalage, k.decalage + k.duree, k.action.commentaire) for k in clips if k.action.commentaire]
    cues = []
    for c in commentaires:
        clip, dedans = None, False
        # Ordre de recherche : action (fin exclue), marge d'un clip (fin exclue), puis le cas pile a la fin
        # d'une action, puis pile a la fin d'un clip. Un @ a la jonction de deux clips va donc au suivant.
        for strict in (True, False):
            for k in clips:
                if k.action.match == c.match and k.action.debut <= c.t and (c.t < k.action.fin if strict else c.t <= k.action.fin):
                    clip, dedans = k, True
                    break
            if clip is None:
                for k in clips:
                    if k.action.match == c.match and k.debut <= c.t and (c.t < k.fin if strict else c.t <= k.fin):
                        clip = k
                        signal.append(f"{c.match} ligne {c.ligne} : commentaire a {fmt_temps(c.t)} dans la marge d'une action, "
                                      f"pas dans l'action elle-meme. Affiche quand meme.")
                        break
            if clip is not None:
                break
        if clip is None:
            ecartee = [a for a in toutes_actions if a.match == c.match and a.debut - MARGES[0] <= c.t <= a.fin + MARGES[1]]
            if ecartee:
                c.motif = f"il tombe dans l'action ligne {ecartee[0].ligne}, ecartee par le filtre --tag"
                signal.append(f"{c.match} ligne {c.ligne} : commentaire a {fmt_temps(c.t)} « {c.texte} » NON AFFICHE : {c.motif}.")
            else:
                c.motif = "hors de toute action selectionnee"
                signal.append(f"{c.match} ligne {c.ligne} : commentaire a {fmt_temps(c.t)} « {c.texte} » NON AFFICHE : {c.motif}.")
            continue
        fin_src_clip = clip.origine + clip.duree
        if c.duree is None:
            limite = min(max(clip.action.fin, c.t + 1.0) if dedans else clip.fin, fin_src_clip)
            fin_src = min(c.t + DUREE_DEFAUT_COMMENTAIRE, limite)
        else:
            fin_src = min(c.t + c.duree, fin_src_clip)
        if c.t < clip.origine:
            c.motif = "avant le debut reel du clip"
            signal.append(f"{c.match} ligne {c.ligne} : commentaire a {fmt_temps(c.t)} avant le debut reel du clip, ignore.")
            continue
        if fin_src - c.t < 0.3:
            c.motif = "trop proche de la fin du clip pour etre lisible (moins de 0,3 s)"
            signal.append(f"{c.match} ligne {c.ligne} : commentaire a {fmt_temps(c.t)} « {c.texte} » NON AFFICHE : {c.motif}.")
            continue
        d = clip.decalage + (c.t - clip.origine)
        cues.append((d, clip.decalage + (fin_src - clip.origine), c.texte))
        c.affiche = True
    return aplatir_cues(cues_clips + cues)


def aplatir_cues(cues):
    """mov_text n'affiche qu'un sous-titre a la fois : on decoupe le temps en tranches sans chevauchement,
    chaque tranche portant les textes actifs (un par ligne, doublons retires, ordre d'arrivee conserve).
    Le .srt, la piste integree et le controle utilisent exactement cette liste."""
    bornes = sorted({round(x, 3) for a, b, _ in cues for x in (a, b)})
    tranches = []
    for p, q in zip(bornes, bornes[1:]):
        if q - p < 0.001:
            continue
        actifs = []
        for a, b, texte in cues:
            if a <= p + 0.0005 and b >= q - 0.0005:
                for ligne in texte.split("\n"):
                    if ligne not in actifs:
                        actifs.append(ligne)
        if not actifs:
            continue
        texte = "\n".join(actifs)
        if tranches and abs(tranches[-1][1] - p) < 0.001 and tranches[-1][2] == texte:
            tranches[-1] = (tranches[-1][0], q, texte)
        else:
            tranches.append((p, q, texte))
    return tranches


def commentaires_hors_action(clips, commentaires, toutes_actions, filtre_tag=False):
    """Commentaires dans aucune action retenue ni marge, et pas dans une action ecartee par --tag (marges comprises)."""
    hors = []
    for c in commentaires:
        if any(k.action.match == c.match and k.debut <= c.t <= k.fin for k in clips):
            continue
        if any(a.match == c.match and a.debut - MARGES[0] <= c.t <= a.fin + MARGES[1] for a in toutes_actions):
            continue  # action ecartee par le filtre : signale plus tard, ce n'est pas un oubli
        if filtre_tag and not any(k.action.match == c.match for k in clips):
            continue  # match sans aucune action portant ce tag : rien a proposer, signale plus tard
        hors.append(c)
    return hors


def proposer_pour_hors_action(hors, clips, infos, tag=None):
    """Propositions (elargir l'action la plus proche, ou creer une action) pour chaque commentaire hors action."""
    out = ["", "=== COMMENTAIRES HORS DE TOUTE ACTION : MONTAGE ARRETE ===",
           "Rien n'a ete produit. Pour chacun, choisis A (elargir l'action la plus proche) ou B (creer une action autour) :"]
    for c in hors:
        duree = DUREE_DEFAUT_COMMENTAIRE if c.duree is None else c.duree
        dv = infos[c.match]["duree"]
        out.append(f"\n- {c.match} ligne {c.ligne} : {fmt_temps(c.t)} « {c.texte} »")
        proches = [k.action for k in clips if k.action.match == c.match]
        if proches:
            a = min(proches, key=lambda x: min(abs(c.t - x.debut), abs(c.t - x.fin)))
            nd, nf = min(a.debut, c.t), min(dv, max(a.fin, c.t + duree))
            nom = a.libelle or "sans libelle"
            out.append(f"    A. Elargir l'action ligne {a.ligne} « {nom} » ({fmt_temps(a.debut)}-{fmt_temps(a.fin)}) "
                       f"en {fmt_temps(nd)}-{fmt_temps(nf)}")
        else:
            out.append("    A. Impossible : aucune action retenue dans ce match.")
        db, fb = math.floor(max(0, c.t - 3)), min(math.ceil(dv), math.ceil(c.t + 7))
        suite = f" (avec le tag « {tag} » pour qu'elle reste retenue)" if tag else ""
        out.append(f"    B. Creer une action {fmt_temps(db)}-{fmt_temps(fb)} (3 s avant, 7 s apres){suite}")
    return "\n".join(out)


def fusionner_clips(clips, signal):
    """Fusionne les clips d'un meme match qui se chevauchent strictement (en chaine : A-B-C).
    Le clip fusionne prend la place du plus ancien dans l'ordre du fichier."""
    res = list(clips)
    modifie = True
    while modifie:
        modifie = False
        for i in range(len(res)):
            for j in range(i + 1, len(res)):
                a, b = res[i], res[j]
                if a.action.match == b.action.match and a.debut < b.fin and b.debut < a.fin:
                    ordre = sorted((a, b), key=lambda k: (k.action.debut, k.action.ligne))
                    x, y = ordre
                    textes = [k.action.commentaire for k in ordre if k.action.commentaire]
                    libelles = [k.action.libelle for k in ordre if k.action.libelle]
                    fusion = Action(
                        a.action.match,
                        min(a.action.debut, b.action.debut), max(a.action.fin, b.action.fin),
                        " / ".join(libelles), tuple(dict.fromkeys(a.action.tags + b.action.tags)),
                        min(a.action.ligne, b.action.ligne), "\n".join(textes),
                        a.action.explicite or b.action.explicite,
                        min(a.action.temps, b.action.temps),
                        tuple(sorted(set(a.action.fusion or (a.action.ligne,)) | set(b.action.fusion or (b.action.ligne,)))))
                    res[i] = Clip(fusion, min(a.debut, b.debut), max(a.fin, b.fin))
                    del res[j]
                    modifie = True
                    break
            if modifie:
                break
    for k in res:  # un seul message par clip fusionne final
        if len(k.action.fusion) > 1:
            lignes = ", ".join(str(n) for n in k.action.fusion)
            signal.append(f"{k.action.match} lignes {lignes} : les clips se chevauchent, fusionnes en un seul "
                          f"({fmt_temps(k.debut)} a {fmt_temps(k.fin)} dans le match).")
    return res


def recapitulatif(clips, commentaires, nb_actions, chapitres, prevu=False):
    """Recapitulatif de fin de montage."""
    manquants = [c for c in commentaires if not c.affiche]
    textes_clips = sum(1 for k in clips if k.action.commentaire)
    lignes = ["", "=== RECAPITULATIF ===",
              f"Actions donnees : {nb_actions}" + (f", clips dans le montage : {len(clips)} (apres fusion)" if len(clips) != nb_actions
                                                  else f", clips dans le montage : {len(clips)}"),
              f"Commentaires de clip (affiches pendant tout le clip) : {textes_clips}",
              f"Commentaires @ demandes : {len(commentaires)}",
              f"Commentaires @ affiches : {len(commentaires) - len(manquants)}",
              f"Chapitres : {chapitres}" + (" (prevus)" if prevu else " (relus dans le fichier)")]
    if manquants:
        lignes.append("Commentaires @ manquants :")
        for c in manquants:
            lignes.append(f"  - {c.match} ligne {c.ligne} : {fmt_temps(c.t)} « {c.texte} » ({c.motif or 'non place'})")
    return "\n".join(lignes)


def temps_srt(s):
    ms = int(round(max(0, s) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    sec, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"


def ecrire_srt(chemin, cues):
    with open(chemin, "w", encoding="utf-8", newline="\n") as f:
        for i, (a, b, texte) in enumerate(cues, 1):
            f.write(f"{i}\n{temps_srt(a)} --> {temps_srt(b)}\n{texte}\n\n")


# --------------------------------------------------------------------------
# Fabrication des videos
# --------------------------------------------------------------------------
def echapper_concat(chemin):
    return chemin.replace("\\", "/").replace("'", "'\\''")


PARAMS_QUALITE = ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2"]
PARAMS_PARTAGE = ["-c:v", "libx264", "-preset", "medium", "-crf", "27", "-pix_fmt", "yuv420p",
                  "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2"]
STYLE_BASE = ("FontName=Arial,Bold=1,PrimaryColour=&H00FFFFFF&,BorderStyle=3,"
              "OutlineColour=&H99000000&,BackColour=&H99000000&,Outline=3,Shadow=0,Alignment=2,MarginV=25")


def titre_chapitre(i, k):
    """« 1 - 1:10 - Trou au bloc » : numero, temps d'origine dans le match, commentaire."""
    texte = (k.action.commentaire or k.action.libelle or "").replace("\n", " / ").strip()
    t = k.action.temps if k.action.temps is not None else k.action.debut
    texte = nettoyer_texte(texte)  # chapitres des anciens fichiers CSV (libelle) : memes regles que les commentaires
    return f"{i} - {fmt_temps(t)} - {texte}" if texte else f"{i} - {fmt_temps(t)}"


def ecrire_chapitres(chemin, clips):
    """Fichier ffmetadata : un chapitre au debut de chaque clip (positions reelles dans le montage)."""
    def esc(s):
        for c in ("\\", "=", ";", "#"):
            s = s.replace(c, "\\" + c)
        return s
    with open(chemin, "w", encoding="utf-8", newline="\n") as f:
        f.write(";FFMETADATA1\n")
        for i, k in enumerate(clips, 1):
            debut = int(round(k.decalage * 1000))
            fin = int(round((k.decalage + k.duree) * 1000))
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={debut}\nEND={fin}\ntitle={esc(titre_chapitre(i, k))}\n")


def fabriquer(clips, cues, sortie, mode, infos, cible_taille, tmp, avertir):
    """Assemble `clips` dans `sortie` (.mp4). mode : copie | precis | incruster | partage."""
    srt_tmp = None
    if cues:
        srt_tmp = os.path.join(tmp, "sous-titres.srt")
        ecrire_srt(srt_tmp, cues)
    meta = os.path.join(tmp, "chapitres.txt")
    ecrire_chapitres(meta, clips)

    # Tous les modes : les clips (coupes ou encodes un par un) sont assembles sans reencodage ; les positions
    # des sous-titres et des chapitres viennent des durees reelles mesurees de chaque clip.
    liste = os.path.join(tmp, "liste.txt")
    with open(liste, "w", encoding="utf-8") as f:
        for k in clips:
            f.write(f"file '{echapper_concat(k.fichier)}'\n")
    cmd = ["-f", "concat", "-safe", "0", "-i", liste]
    if srt_tmp and mode in ("copie", "precis"):  # piste de sous-titres (en incruster/partage : texte grave)
        cmd += ["-i", srt_tmp, "-i", meta, "-map", "0:v", "-map", "0:a?", "-map", "1:0", "-map_chapters", "2",
                "-c", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=fra", "-disposition:s:0", "default"]
    else:
        cmd += ["-i", meta, "-map", "0:v", "-map", "0:a?", "-map_chapters", "1", "-c", "copy"]
    ffmpeg(*cmd, "-movflags", "+faststart", sortie)


def encoder_clips(clips, commentaires, toutes_actions, mode, infos, cible_taille, tmp):
    """Modes --precis / --incruster / --partage : chaque clip est coupe exactement (-ss/-t en entree) et encode
    seul ; en --incruster/--partage ses sous-titres sont graves clip par clip (temps locaux au clip)."""
    w, h = (1280, 720) if mode == "partage" else cible_taille
    meme_taille = len({infos[k.action.match]["format"][1:3] for k in clips}) == 1
    for i, k in enumerate(clips, 1):
        k.origine, k.decalage, k.duree = k.debut, 0.0, k.fin - k.debut   # temps locaux, provisoires
        filtres = []
        if mode == "partage" or not meme_taille:
            filtres.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
        filtres.append("setsar=1")
        if mode in ("incruster", "partage"):
            locaux = placer_commentaires(
                [k], [c for c in commentaires if c.match == k.action.match and k.debut <= c.t <= k.fin],
                toutes_actions, [])
            if locaux:
                ecrire_srt(os.path.join(tmp, f"clip_{i:03d}.srt"), locaux)
                taille = 14 if mode == "partage" else 12
                filtres.append(f"subtitles=clip_{i:03d}.srt:force_style='{STYLE_BASE},FontSize={taille}'")
        k.fichier = os.path.join(tmp, f"clip_{i:03d}.mp4")
        params = PARAMS_PARTAGE if mode == "partage" else PARAMS_QUALITE
        ffmpeg("-ss", f"{k.debut:.3f}", "-t", f"{k.fin - k.debut:.3f}", "-i", chemin_video(k.action.match),
               "-vf", ",".join(filtres), "-map", "0:v:0", "-map", "0:a:0?", *params, k.fichier, cwd=tmp)
        k.duree = duree_fichier(k.fichier)   # duree reelle


def controler_sous_titres(sortie, cues, mode):
    """Erreur claire si des commentaires etaient attendus et que la piste manque ou est vide."""
    if not cues:
        return
    if mode in ("incruster", "partage"):
        return  # texte grave dans l'image : pas de piste a controler
    out = lancer([FFPROBE, "-v", "error", "-select_streams", "s", "-count_packets",
                  "-show_entries", "stream=codec_name:stream_disposition=default",
                  "-of", "json", sortie])
    pistes = json.loads(out).get("streams", [])
    if not pistes:
        raise Erreur(f"{os.path.basename(sortie)} : {len(cues)} commentaire(s) attendu(s) mais AUCUNE piste de sous-titres dans le fichier.")
    if len(pistes) != 1:
        raise Erreur(f"{os.path.basename(sortie)} : {len(pistes)} pistes de sous-titres au lieu d'une seule.")
    p = pistes[0]
    if p.get("codec_name") != "mov_text":
        raise Erreur(f"{os.path.basename(sortie)} : piste de sous-titres au format {p.get('codec_name')} au lieu de mov_text.")
    paquets = lancer([FFPROBE, "-v", "error", "-select_streams", "s:0", "-show_entries", "packet=size",
                      "-of", "csv=p=0", sortie]).split()
    reels = sum(1 for t in paquets if t.isdigit() and int(t) > 2)  # les paquets de 2 octets sont des vides
    if reels != len(cues):
        raise Erreur(f"{os.path.basename(sortie)} : la piste de sous-titres contient {reels} sous-titre(s) reel(s) "
                     f"pour {len(cues)} attendu(s).")
    if not p.get("disposition", {}).get("default"):
        raise Erreur(f"{os.path.basename(sortie)} : la piste de sous-titres n'est pas marquee par defaut.")


def lire_chapitres(sortie):
    """Chapitres reellement ecrits dans le fichier : liste de (debut s, titre)."""
    out = lancer([FFPROBE, "-v", "error", "-show_chapters", "-of", "json", sortie])
    res = []
    for c in json.loads(out).get("chapters", []):
        res.append((float(c["start_time"]), c.get("tags", {}).get("title", "")))
    return res


def controler_chapitres(sortie, clips):
    """Dans tous les modes : un chapitre par clip, au debut reel du clip, avec le bon titre."""
    ecrits = lire_chapitres(sortie)
    nom = os.path.basename(sortie)
    if len(ecrits) != len(clips):
        raise Erreur(f"{nom} : {len(ecrits)} chapitre(s) dans le fichier pour {len(clips)} clip(s).")
    for i, (k, (t, titre)) in enumerate(zip(clips, ecrits), 1):
        if abs(t - k.decalage) > CHAPITRE_DECALAGE_MAX:
            raise Erreur(f"{nom} : le chapitre {i} commence a {t:.2f} s au lieu de {k.decalage:.2f} s (debut reel du clip).")
        if titre != titre_chapitre(i, k):
            raise Erreur(f"{nom} : titre du chapitre {i} « {titre} » au lieu de « {titre_chapitre(i, k)} ».")


def preparer_clips_copie(clips, infos, cles_par_match, tmp):
    """Coupe chaque clip sans reencodage, a partir de l'image cle precedente."""
    for i, k in enumerate(clips, 1):
        m = k.action.match
        cles = cles_par_match[m]
        precedentes = [c for c in cles if c <= k.debut + 1e-3]
        origine = precedentes[-1] if precedentes else (cles[0] if cles else 0.0)
        k.origine = origine
        k.fichier = os.path.join(tmp, f"clip_{i:03d}.mp4")
        ffmpeg("-ss", f"{origine + 0.01:.3f}", "-i", chemin_video(m), "-t", f"{k.fin - origine:.3f}",
               "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-avoid_negative_ts", "make_zero", k.fichier)
        k.duree = duree_fichier(k.fichier)


def attribuer_decalages(clips):
    t = 0.0
    for k in clips:
        k.decalage = t
        t += k.duree


# --------------------------------------------------------------------------
# Programme principal
# --------------------------------------------------------------------------
def construire_parser():
    p = argparse.ArgumentParser(description="Montages d'actions de volley a partir de timecodes.")
    p.add_argument("match", nargs="?", help="nom, date ou adversaire (ex. paris, 2026-11-14_paris)")
    p.add_argument("--precis", action="store_true", help="coupe exacte a l'image, reencodage H.264 haute qualite")
    p.add_argument("--incruster", action="store_true", help="grave les commentaires dans l'image")
    p.add_argument("--partage", action="store_true", help="version joueuses : 720p, commentaires graves, leger")
    p.add_argument("--tag", help="ne garde que les actions portant ce tag")
    p.add_argument("--tous", action="store_true", help="cherche dans tous les matchs")
    p.add_argument("--marge", nargs="+", type=float, metavar="S",
                   help="secondes avant le debut de chaque clip (defaut 1). Un second nombre = secondes apres la fin, "
                        "pour les plages debut-fin seulement (defaut 0)")
    p.add_argument("--duree", type=float, metavar="S", default=DUREE_APRES_DEFAUT,
                   help="pour un temps seul : secondes de clip apres ce temps (defaut 9)")
    p.add_argument("--separes", action="store_true", help="un fichier par action")
    p.add_argument("--verifier", action="store_true", help="controle les timecodes, ne produit rien")
    p.add_argument("--accepter-reencodage", action="store_true",
                   help="autorise le reencodage quand les matchs ont des formats differents")
    return p


def main(argv=None):
    args = construire_parser().parse_args(argv)
    init_outils()

    modes = [m for m in ("precis", "incruster", "partage") if getattr(args, m)]
    if len(modes) > 1:
        raise Erreur("Choisis un seul mode parmi --precis, --incruster, --partage.")
    mode = modes[0] if modes else "copie"

    if args.marge is None:
        avant, apres = MARGE_AVANT_DEFAUT, MARGE_APRES_DEFAUT
    elif len(args.marge) == 1:
        avant, apres = args.marge[0], MARGE_APRES_DEFAUT
    elif len(args.marge) == 2:
        avant, apres = args.marge
    else:
        raise Erreur("--marge prend un ou deux nombres.")
    global MARGES
    if not (math.isfinite(avant) and math.isfinite(apres)) or avant < 0 or apres < 0:
        raise Erreur("--marge doit etre un nombre positif ou nul.")
    if not math.isfinite(args.duree) or args.duree <= 0:
        raise Erreur("--duree doit etre un nombre strictement positif.")

    MARGES = (avant, apres)
    if args.tous:
        matchs = [os.path.splitext(v)[0] for v in videos_disponibles()]
        if not matchs:
            raise Erreur("Aucune video dans matchs/.")
    elif args.match:
        matchs = [trouver_match(args.match)]
    else:
        raise Erreur("Indique un match (ex. python montage.py paris) ou utilise --tous.")

    problemes, signal = [], []
    infos, toutes_actions, commentaires = {}, [], []
    sans_fichier = []
    for m in matchs:
        tc = chemin_timecodes(m)
        if not os.path.isfile(tc):
            sans_fichier.append(m)
            continue
        infos[m] = sonder(chemin_video(m))
        a, c = lire_fichier_timecodes(tc, m, infos[m]["duree"], problemes, args.duree)
        toutes_actions += a
        commentaires += c
    if sans_fichier:
        if args.tous:
            print("Matchs sans fichier de timecodes (ignores) : " + ", ".join(sans_fichier))
        else:
            raise Erreur(f"Pas de fichier timecodes/{sans_fichier[0]}.csv. Cree-le puis relance.")
    if not infos:
        raise Erreur("Aucun fichier de timecodes trouve.")

    if args.tag:
        t = sans_accents(args.tag).lower().strip()
        retenues = [a for a in toutes_actions if t in [sans_accents(x) for x in a.tags]]
    else:
        retenues = list(toutes_actions)

    print(f"Matchs : {', '.join(infos)}")
    print(f"Actions : {len(toutes_actions)} lues, {len(retenues)} retenues"
          + (f" (tag « {args.tag} »)" if args.tag else "")
          + f" ; {len(commentaires)} commentaire(s).")
    if problemes:
        print("\n=== LIGNES A VERIFIER ===")
        for p in problemes:
            print(" - " + p)
    if not retenues:
        raise Erreur("Aucune action a monter" + (f" avec le tag « {args.tag} »." if args.tag else "."))

    # Formats des videos
    formats = {m: infos[m]["format"] for m in infos if any(a.match == m for a in retenues)}
    formats_distincts = set(formats.values())
    melange = len(formats_distincts) > 1
    for m, i in infos.items():
        if i["rotation"]:
            print(f"Attention : {m} porte une rotation de {i['rotation']} degres.")
    if melange:
        print("\n=== FORMATS DIFFERENTS ===")
        for m, f in formats.items():
            print(f" - {m} : {decrire_format(f)}")
        if not args.accepter_reencodage and not args.verifier:
            raise Erreur("Ces matchs ont des formats differents : un reencodage est necessaire pour les assembler "
                         "(plus lent, et la coupe devient exacte a l'image). Relance avec --accepter-reencodage "
                         "si tu es d'accord, ou monte les matchs un par un.")
        if mode == "copie":
            mode = "precis"
            print("-> mode passe en --precis (reencodage) pour assembler ces formats.")

    # Clips (avec marges)
    clips = []
    for a in retenues:
        d = infos[a.match]["duree"]
        clips.append(Clip(a, max(0.0, a.debut - avant), min(d, a.fin + (apres if a.explicite else 0.0))))
    nb_actions = len(clips)
    clips = fusionner_clips(clips, signal)

    hors = commentaires_hors_action(clips, commentaires, toutes_actions, bool(args.tag))
    if hors:
        print(proposer_pour_hors_action(hors, clips, infos, args.tag))
        print("\nReponds avec ton choix (A ou B pour chaque commentaire) : je modifie le fichier de timecodes puis je relance.")
        return 3

    if args.verifier:
        # Place les commentaires sur des clips fictifs (sans coupe) pour detecter les non-affiches.
        for k in clips:
            k.origine, k.duree = k.debut, k.fin - k.debut
        attribuer_decalages(clips)
        placer_commentaires(clips, commentaires, toutes_actions, signal)
        if signal:
            print("\n=== A SIGNALER ===")
            for s in signal:
                print(" - " + s)
        print(recapitulatif(clips, commentaires, nb_actions, len(clips), prevu=True))
        print("\nVerification terminee. Rien n'a ete produit.")
        return 0

    # Noms de sortie
    os.makedirs(DOSSIER_MONTAGES, exist_ok=True)
    theme = slug(args.tag) if args.tag else "montage"
    base = (f"tous-matchs_{theme}" if args.tous or len(infos) > 1 else f"{list(infos)[0]}_{theme}")
    suffixe = {"copie": "", "precis": "_precis", "incruster": "_incruste", "partage": "_partage"}[mode]
    cible = (infos[clips[0].action.match]["largeur"] or 1920, infos[clips[0].action.match]["hauteur"] or 1080)
    cible = (cible[0] // 2 * 2, cible[1] // 2 * 2)

    keyframes = {}
    if mode == "copie":
        for m in formats:
            print(f"Lecture des images cles de {m}...")
            keyframes[m] = images_cles(chemin_video(m), infos[m]["debut"])

    with tempfile.TemporaryDirectory(prefix="montage_") as tmp:
        if mode == "copie":
            print(f"Coupe de {len(clips)} clip(s) sans reencodage...")
            preparer_clips_copie(clips, infos, keyframes, tmp)
        else:
            print(f"Montage avec reencodage ({mode}), {len(clips)} clip(s) : cela peut prendre un moment...")
            encoder_clips(clips, commentaires, toutes_actions, mode, infos, cible, tmp)
            for k in clips:  # clips encodes a partir du debut exact : l'origine est le debut demande
                k.origine = k.debut
        resultats = []

        if args.separes:
            dossier = os.path.join(DOSSIER_MONTAGES, nom_libre(DOSSIER_MONTAGES, base + "_separes" + suffixe, [""]))
            os.makedirs(dossier)
            groupes = []
            for n, k in enumerate(clips, 1):
                court = slug(k.action.libelle)[:40].strip("-") if k.action.libelle else fmt_temps(k.action.debut).replace(':', 'm')
                nom = f"{n:02d}_{k.action.match}_{court}"
                groupes.append(([k], os.path.join(dossier, nom + ".mp4")))
        else:
            nom = nom_libre(DOSSIER_MONTAGES, base + suffixe, [".mp4", ".srt"])
            groupes = [(clips, os.path.join(DOSSIER_MONTAGES, nom + ".mp4"))]

        # Les commentaires non places sont signales une seule fois, sur l'ensemble.
        attribuer_decalages(clips)
        placer_commentaires(clips, commentaires, toutes_actions, signal)

        chapitres_lus = 0
        try:
          for n, (groupe, sortie) in enumerate(groupes, 1):
            attribuer_decalages(groupe)
            cles = placer_commentaires(groupe, [c for c in commentaires if any(
                k.action.match == c.match and k.debut <= c.t <= k.fin for k in groupe)], toutes_actions, [])
            sous = os.path.join(tmp, f"g{n}")
            os.makedirs(sous, exist_ok=True)
            fabriquer(groupe, cles, sortie, mode, infos, cible, sous, signal)
            try:
                controler_sous_titres(sortie, cles, mode)
                controler_chapitres(sortie, groupe)
            except Erreur:
                os.remove(sortie)  # ne pas laisser un fichier defectueux dans montages/
                raise
            if cles and mode != "partage":
                ecrire_srt(os.path.splitext(sortie)[0] + ".srt", cles)
            chapitres_lus += len(lire_chapitres(sortie))
            resultats.append(sortie)
        except Erreur:
            # ne laisser aucun resultat partiel dans montages/
            if args.separes:
                shutil.rmtree(dossier, ignore_errors=True)
            else:
                for _f in (groupes[0][1], os.path.splitext(groupes[0][1])[0] + ".srt"):
                    if os.path.exists(_f):
                        os.remove(_f)
            raise

    if signal:
        print("\n=== A SIGNALER ===")
        for s in dict.fromkeys(signal):
            print(" - " + s)
    print(recapitulatif(clips, commentaires, nb_actions, chapitres_lus))
    print("\nTermine :")
    for r in resultats:
        print(f"  {r}  ({os.path.getsize(r) / 1e6:.1f} Mo)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Erreur as e:
        print(f"\nERREUR : {e}", file=sys.stderr)
        sys.exit(1)
