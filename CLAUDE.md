# volley-video — analyse vidéo de match

## Contexte
Outil du VBG (Volley-Ball Grégorien) : une page web qui enchaîne des extraits d'un match de
volley, avec une consigne pour chaque action, pour revoir les actions clés avec les joueurs de
la N3M. Français uniquement.
Projet voisin : volley-motion (séances N3M animées, dépôt Kind-Tree/volley-motion).
Les séances animées vivent dans volley-motion, pas ici.

## Fichiers
- `index.html` : toute la page (HTML, CSS, JavaScript), sans dépendance à installer.
  Les réglages et la liste des actions sont en haut du `<script>` : `VIDEO`, `AVANT`, `APRES`,
  `AFFICHER_NOTES`, `ACTIONS` (`set`, `temps`, `fin` facultative, `note`).
- `README.md` : utilisation, format des actions, notes techniques (modes YouTube / MP4,
  `jouer()`, `bornes()`, garde-fous `enLecture`, `extraitActif`, `jeton`). Le lire avant de
  toucher au code de lecture.

## Commandes
Ouvrir `index.html` par double-clic (Chrome, Edge ou Firefox). Page en ligne :
https://kind-tree.github.io/volley-video/ (GitHub Pages, branche `main`, chaque push publie).

## Règles
- Le dépôt et la page sont publics : rien de confidentiel dans `index.html`, notes comprises
  (pas de données personnelles de joueurs).
- Ne jamais ajouter de vidéo au dépôt (`.gitignore` exclut les formats vidéo) ; données brutes
  éventuelles dans `donnees/`, jamais versionné.
- Une page = un seul match : ses timecodes ne valent que pour sa vidéo.
- `temps` et `fin` au format `m:ss` ou `mm:ss` ; garder guillemets et virgules, sinon la page
  ne se charge plus.
- Aucune dépendance externe ; le mode MP4 doit marcher hors ligne.
- DA du club : orange (#F26910) et noir, Arial.
- Vérifier dans le navigateur après une modification, onglet visible (voir Notes techniques
  du README : `requestAnimationFrame` est suspendu en arrière-plan).
- Petits commits, un sujet par commit, messages en français.

## Synchronisation (travail sur plusieurs appareils)
- Dépôt GitHub : Kind-Tree/volley-video, une seule branche : main.
- En début de session : `git pull` avant toute modification.
- En fin de session : commit et push de tout le travail.
