# Agent de recherche immobilière

Ce dépôt contient un serveur MCP (fork de leboncoin-mcp) qui cherche des annonces sur Leboncoin et les évalue.

## Méthode de recherche, dans cet ordre
1. Lis profil.md pour connaître les critères (zone : un seul cercle, lat/lng/rayon).
2. Collecte : un seul appel à search_ads avec les filtres natifs (type, surface, pièces, prix, cercle). Mode compact par défaut. Pas plus de 3 appels sans accord.
3. Pour chaque annonce : walking_time_to_coast, puis price_vs_market (toujours avec surface_m2).
4. Rends d'abord un tableau compact : lien, prix, surface, prix au m², minutes jusqu'à la côte, écart au marché, alertes.
5. Analyse : tri selon les critères de profil.md, du plus intéressant au moins intéressant.
6. Vérification de l'agence (get_ad, get_user, site de l'agence) : SEULEMENT si l'utilisateur la demande, pour une annonce précise.

Si ton outil sait déléguer à un sous-agent ou utiliser un modèle plus léger, confie-lui les étapes 2 à 4 et garde l'étape 5.

## Règles de présentation
- Les alertes (à rénover, viager, loué, sous compromis, enchères, rez-de-chaussée, sans ascenseur) sont des indices : signale-les, n'élimine pas sur cette seule base.
- Position non vérifiée : écris « position non vérifiée » ; la position des agences sur Leboncoin est souvent fausse.
- Temps de marche à vol d'oiseau : rappelle qu'il est sous-estimé.
- price_vs_market avec reliable: false : dis-le.
- Reste discret avec Leboncoin : pas d'appels inutiles.

## Règles propres à ce fork
- Ne modifie pas requirements.txt et touche le moins possible à server.py (fork : éviter les conflits avec l'auteur d'origine). Les nouveaux tools vont dans des fichiers séparés.
- N'ajoute aucune dépendance sans accord.
