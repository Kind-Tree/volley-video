# Volley vidéo : montages d'actions

## Le projet
Le coach filme ses matchs et veut des montages d'actions à partir de timecodes et de commentaires donnés en langage libre. Deux outils cohabitent dans ce dossier :
- `index.html` : page web (GitHub Pages, dépôt public) qui enchaîne des extraits. Voir `README.md`. Ne pas y toucher sans demande.
- `montage.py` : outil de montage (Python + ffmpeg), décrit ci-dessous.

L'utilisateur est coach, pas développeur. Explique en français simple.

## Dossiers
- `matchs/` : vidéos brutes, une par match, **ne jamais les modifier**. Nommage `date_adversaire`, ex. `2026-11-14_maen-roch.mp4` (minuscules, tirets, sans accents).
- `timecodes/` : un `.csv` par match, même nom que la vidéo.
- `montages/` : vidéos générées. Jamais d'écrasement : `_2`, `_3`… si le nom existe.
- `matchs/`, `montages/`, `timecodes/`, `*.srt` sont dans `.gitignore` (dépôt public : rien de tout cela ne doit partir en ligne). Le dossier n'est pas (encore) un dépôt git local ; l'envoi GitHub web ignore `.gitignore` : ne jamais téléverser ces dossiers à la main.
- Ne jamais mettre de noms de joueuses dans les fichiers versionnés (CLAUDE.md, README, index.html, docstring de `montage.py`).
- Ne toucher à aucune vidéo hors de ce dossier sans demande explicite.

## Format de `timecodes/<match>.csv` (une action par ligne)
```
1:10 -> Trou au bloc à couvrir #bloc
1:10 +15 -> texte
1:10-1:25 -> texte
4m58 -> texte
@1:10,Texte à un instant précis
@1:10,Texte à un instant précis,6
# ligne ignorée
```
- **Un temps seul = le début du point.** Le clip commence 1 s avant et dure 9 s après (réglages par défaut : `--marge` 1 s avant, `--duree` 9 s). `1:10 +15` = 15 s après le temps (au lieu de 9, 1 s avant gardée) ; `1:10-1:25` = plage explicite (1 s avant le début, fin exacte ; le 2e nombre de `--marge` ajoute du temps après la fin des plages, 0 par défaut). Temps en `mm:ss`, `h:mm:ss`, secondes ou `4m58`.
- **Le texte après `->` s'affiche pendant tout le clip** (sous-titre) et titre le chapitre. Les `#mots` sont des **tags** pour `--tag`, retirés du texte affiché (donc pas de `#` dans un texte). Texte et tags facultatifs.
- **Quand le coach donne des lignes « heure + texte » : ce sont des clips avec commentaire** (syntaxe `->`), pas des lignes `@`. Les lignes `@temps,texte[,durée]` (sous-titre à un instant précis, 4 s par défaut) ne s'écrivent que si le coach les demande explicitement ; le dernier champ y est une durée s'il est numérique. Si un clip et un `@` se superposent, les deux textes s'affichent l'un sous l'autre.
- Ancien format CSV `debut,fin,libelle,tags` toujours lu (le libellé n'est alors affiché ni en sous-titre, seulement dans le chapitre). Guillemets CSV acceptés pour une virgule dans un texte `@`. Fichier UTF-8 (BOM accepté).
- **Clips qui se chevauchent (strictement) : fusionnés en un seul**, en chaîne (A-B-C), avec avertissement. Le clip fusionné montre les commentaires l'un sous l'autre, le chapitre les joint par « / » avec le plus ancien temps. Deux clips qui se touchent restent séparés.
- Fin au-delà de la vidéo : ramenée à la fin (signalé pour une plage explicite).

## montage.py
`python montage.py <match>` (nom, date ou adversaire partiel suffit) :
- défaut : coupe sans réencodage, début calé sur l'image clé précédente (≈ 1 s d'avance max sur cette caméra), sous-titres en piste + `.srt` à côté, chapitres ;
- `--precis` : coupe exacte, H.264 CRF 17 ; `--incruster` : commentaires gravés ; `--partage` : 720p léger, gravés, nom en `_partage` (pas de .srt) ;
- `--tag X`, `--tous`, `--marge [avant] [après]`, `--duree S`, `--separes` (un dossier `..._separes/`), `--verifier` (contrôle les timecodes sans rien produire) ;
- formats de matchs différents : arrêt avec explication ; `--accepter-reencodage` pour continuer (passe en `--precis`).
- **Chapitres** dans tous les modes (aussi `--separes`) : un par clip, titre « numéro - temps d'origine - commentaire », positions = début réel mesuré de chaque clip. Contrôlés après chaque montage (nombre, positions, titres) ; ffprobe voit en plus une piste « data » : c'est la piste de chapitres MP4 (normal). En `--precis`, ffprobe peut avertir « Referenced QT chapter track not found » : sans conséquence, les chapitres sont lus.
- **Technique** : en copie, chaque clip est coupé en `-c copy`. En réencodage, chaque clip est coupé exactement (`-ss`/`-t` en entrée) et encodé SEUL (sous-titres gravés clip par clip), puis tous sont assemblés en `-c copy` ; sous-titres et chapitres sont placés d'après la durée réelle mesurée (ffprobe) de chaque clip. Ne PAS revenir au concat demuxer avec `inpoint`/`outpoint` (pas exact à l'image en H.264) ni au filtre `concat` en une passe (dérive d'environ 0,1 s sur 12 clips, mesurée).
- Limites connues : en réencodage, chapitres et sous-titres arrivent environ 56 ms (2 images) avant la première image de chaque clip (constant, ne s'accumule pas) ; avec `--tous`, le titre d'un chapitre n'indique pas le match. Dans les textes, `\` devient `/` et `{ }` deviennent `( )` (sinon ffmpeg/libass les interprètent).
- Sous-titres : piste `mov_text` (langue `fra`, par défaut) identique au `.srt`. mov_text n'affiche qu'un sous-titre à la fois : le temps est découpé en tranches sans chevauchement, chaque tranche portant tous les textes actifs (un par ligne). Contrôle automatique après chaque montage ; en cas d'échec le fichier est supprimé (`--separes` : tout le dossier) et une erreur s'affiche. Un montage sans commentaire n'a pas de piste (normal).
- **Commentaire `@` hors de toute action : ne jamais l'écarter en silence.** Le script s'arrête avant le montage (code 3, rien produit, aussi avec `--verifier`) et propose pour chacun : A = élargir l'action la plus proche, B = créer une action autour (3 s avant, 7 s après). Attendre la réponse du coach, modifier `timecodes/<match>.csv`, relancer. (Un `@` dans une action seulement écartée par `--tag` n'arrête pas : signalé et compté manquant.)
- **Récapitulatif obligatoire en fin de montage** (et de `--verifier`) : actions données et clips après fusion, commentaires de clip, commentaires `@` demandés/affichés avec la liste des manquants et leur motif, nombre de chapitres relus dans le fichier. Le répéter au coach.
- Ménage : après un montage validé, ne garder que le dernier montage valide dans `montages/` (demander avant de supprimer, ne jamais toucher à `matchs/` ni `timecodes/`).
- ffmpeg et Python installés via winget le 2026-11-15 (`Python.Python.3.12`, `Gyan.FFmpeg`). Si `python`/`ffmpeg` sont introuvables dans un terminal ouvert avant l'installation : le rouvrir. `montage.py` retrouve ffmpeg tout seul.
- Caméra actuelle : H.264 Main 1080p ≈ 30 i/s (variable), AAC 44,1 kHz, sans rotation, image clé toutes les secondes. La coupe sans réencodage y fonctionne bien.

## Au quotidien : demande d'edit
Quand le coach donne un match et des lignes « temps + commentaire » :
1. retrouver la vidéo par adversaire ou date dans `matchs/` (demander en cas de doute) ;
2. créer ou compléter `timecodes/<match>.csv` avec la syntaxe `temps -> commentaire #tags` (sauvegarder l'ancien fichier avant de le convertir ; ne pas écraser ses lignes sans accord) ;
3. lancer `python montage.py <match> --verifier`, signaler les lignes incohérentes (fin avant début, temps au-delà de la durée) ;
4. lancer le montage demandé et donner le chemin du fichier produit, avec le récapitulatif et les avertissements.
Une vidéo posée à la racine ou mal nommée : proposer de la ranger/renommer, ne pas le faire seul.

## Organisation des agents
Reprise simplifiée de `volley-tournoi` (agents globaux dans `C:\Users\samue\.claude\agents\`, aucun agent propre à ce projet) :
- Session principale (Sonnet) : construit et lance les montages, fait tout le travail de préparation des timecodes.
- Conseiller (advisor) : à consulter AVANT un choix de structure (format des fichiers, architecture du script, changement de technique de coupe) et quand une erreur revient deux fois.
- `checker` (Opus, lecture seule, sur copie hors projet) : à lancer en fin de travail sur `montage.py`, puis corriger ce qu'il trouve. Il ne corrige rien lui-même. Après correction, rejouer ses cas ; dire clairement si lui-même n'a pas fait de seconde passe.
- `Explore` (Haiku) : recherche de fichiers ou de documentation uniquement.
- Pas d'autre sous-agent pour écrire du code. Pas d'agent `preparateur-edit` (décision du coach, 2026-11-15).
- Édition de `montage.py` : utiliser Edit/Write, pas de scripts de remplacement dans un heredoc Bash (les `\n` y sont abîmés).
