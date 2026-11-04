# Analyse vidéo volley

Page web qui enchaîne des extraits d'un match de volley, avec une consigne pour chaque action. Elle sert à revoir les actions clés avec les joueurs.

- **Page en ligne :** https://kind-tree.github.io/volley-video/
- **Fichier unique :** tout tient dans `index.html` (HTML, CSS et JavaScript, sans dépendance à installer).

## Utilisation

Commandes : **Précédent**, **Lancer / Rejouer**, **Suivant**, menu des actions, vitesse (x0,25, x0,5, x1) et **Enchaîner automatiquement**.

Deux sources vidéo, avec les mêmes actions :

| Mode | Source | Internet |
|---|---|---|
| YouTube (par défaut) | la vidéo définie par `VIDEO` | nécessaire |
| MP4 local | un fichier choisi avec **Choisir un fichier MP4** | inutile si la page est ouverte depuis le fichier `index.html` |

En mode MP4, la vidéo est lue sur l'ordinateur et n'est jamais envoyée en ligne. Format conseillé : MP4 H.264/AAC. Le bouton **Revenir à YouTube** n'apparaît qu'en mode MP4.

### Sans internet

Le lien en ligne ne s'ouvre pas sans connexion. Il faut ouvrir le fichier `index.html` par double-clic (Chrome, Edge ou Firefox), puis choisir le MP4. Le message « YouTube est indisponible… » est alors normal.

## Modifier les actions

Tout se règle en haut du `<script>` de `index.html`.

```js
const VIDEO = "…";           // identifiant de la vidéo YouTube
const AVANT = 3;             // secondes montrées avant chaque timecode
const APRES = 10;            // secondes montrées après chaque timecode
const AFFICHER_NOTES = true; // false = notes masquées à l'écran (elles restent lisibles dans le code)

const ACTIONS = [
  { set: 1, temps: "3:40", note: "Consigne affichée" },
  { set: 2, temps: "17:00", fin: "17:30", note: "fin = fin précise de l'extrait" },
];
```

- `temps` et `fin` s'écrivent en `m:ss` ou `mm:ss`.
- Chaque extrait va de `temps − AVANT` à `temps + APRES`, ou jusqu'à `fin` si elle est indiquée.
- Garder les guillemets et la virgule en fin de ligne, sinon la page ne se charge plus.

**Depuis le site GitHub :** ouvrir `index.html`, cliquer sur le crayon, modifier, puis **Commit changes**. La page en ligne est à jour en une ou deux minutes.

**Depuis un ordinateur :** faire `git pull` avant de commencer, puis `git add`, `git commit` et `git push` à la fin.

## Publication

GitHub Pages publie la branche `main`, dossier `/ (root)`. Chaque push sur `main` met la page en ligne à jour.

## Règles du dépôt

- **Le dépôt est public :** tout ce qui est écrit dans `index.html`, notes comprises, est lisible par tous.
- **Ne jamais ajouter de vidéo au dépôt.** `.gitignore` exclut `*.mp4`, `*.mov`, `*.mkv`, `*.webm`, `*.m4v` et `*.avi`.
- Une page correspond à un seul match : ses timecodes ne valent que pour sa vidéo.

## Notes techniques

Pour qui reprend le code, humain ou assistant.

- Une variable `mode` (`"youtube"` ou `"mp4"`) choisit la source. Toutes les commandes passent par `jouer(position)`, qui appelle `jouerYoutube()` ou `jouerMp4()`. Les bornes des extraits viennent toutes de `bornes(action)`.
- **YouTube :** l'API signale parfois deux fois la fin d'un extrait. Le drapeau `enLecture` évite de sauter une action. La première action s'affiche sans attendre l'API. Si l'API échoue ou ne répond pas en 15 s (`DELAI_YOUTUBE`), un message invite à choisir un MP4.
- **MP4 :** le fichier est lu via `URL.createObjectURL`. La fin d'extrait est surveillée avec `requestAnimationFrame` pendant la lecture, et `timeupdate` sert de secours. `extraitActif` empêche tout double déclenchement (fin, `ended`, clics rapides), et `jeton` invalide les minuteurs devenus obsolètes. La fin est limitée à la durée du fichier. Une action qui commence après la fin du fichier affiche un message, puis passe à la suivante après 2 s si l'enchaînement est coché. L'URL est libérée (`revokeObjectURL`) quand on change de fichier ou qu'on revient à YouTube.
- **Tests :** quand l'onglet n'est pas affiché à l'écran, le navigateur suspend `requestAnimationFrame`. Seul `timeupdate` arrête alors l'extrait, avec jusqu'à environ 0,25 s de retard. Il faut mesurer la précision de fin avec la page visible.
