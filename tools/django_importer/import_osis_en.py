# preaching_app_project/preacher_helper/management/commands/import_osis.py

# preacher_helper/management/commands/import_osis.py
# -*- coding: utf-8 -*-

"""
Commande de gestion Django pour importer un fichier de la Bible au format OSIS XML
dans la base de données.

Ce script est conçu pour être robuste et efficace en mémoire, en utilisant une
analyse itérative (iterparse) du fichier XML. Il gère la structure spécifique
du fichier Sg1910_v11n.osis.xml, qui utilise des balises "jalons" (milestones)
pour délimiter les versets.
"""

import os
import re
from lxml import etree
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings

from preacher_helper.models import Book, Chapter, Verse, WordStrong

# Espace de noms XML pour le format OSIS.
OSIS_NS = "http://www.bibletechnologies.net/2003/OSIS/namespace"

# Liste des identifiants OSIS des livres dans leur ordre canonique.
# Cette liste est cruciale pour assurer que les livres sont triés correctement
# dans l'application, et non par ordre alphabétique.
CANONICAL_OSIS_ID_ORDER = [
    "Gen", "Exod", "Lev", "Num", "Deut", "Josh", "Judg", "Ruth", "1Sam", "2Sam",
    "1Kgs", "2Kgs", "1Chr", "2Chr", "Ezra", "Neh", "Esth", "Job", "Ps", "Prov",
    "Eccl", "Song", "Isa", "Jer", "Lam", "Ezek", "Dan", "Hos", "Joel", "Amos",
    "Obad", "Jonah", "Mic", "Nah", "Hab", "Zeph", "Hag", "Zech", "Mal", "Matt",
    "Mark", "Luke", "John", "Acts", "Rom", "1Cor", "2Cor", "Gal", "Eph", "Phil",
    "Col", "1Thess", "2Thess", "1Tim", "2Tim", "Titus", "Phlm", "Heb", "Jas",
    "1Pet", "2Pet", "1John", "2John", "3John", "Jude", "Rev"
]
class Command(BaseCommand):
    help = 'Importe un fichier XML OSIS de manière robuste.'

    def handle(self, *args, **options):
        xml_file_path = os.path.join(settings.BASE_DIR, 'osis_origine', 'Sg1910_v11n.osis.xml')
        
        if not os.path.exists(xml_file_path):
            raise CommandError(f"Le fichier XML n'a pas été trouvé : {xml_file_path}")

        self.stdout.write(self.style.SUCCESS(f"Début de l'importation robuste..."))

        try:
            # La transaction atomique garantit que si une erreur se produit,
            # la base de données est restaurée à son état précédent.
            with transaction.atomic():
                self.run_import(xml_file_path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise CommandError(f"Une erreur fatale est survenue: {e}")

        self.stdout.write(self.style.SUCCESS("\nImportation terminée avec succès !"))

    def run_import(self, xml_file_path):
        # Le script est idempotent : il nettoie les anciennes données avant chaque import.
        self.stdout.write("Nettoyage des anciennes données...")
        WordStrong.objects.all().delete()
        Verse.objects.all().delete()
        Chapter.objects.all().delete()
        Book.objects.all().delete()
        self.stdout.write("Anciennes données supprimées.")

        # Variables d'état pour suivre la position dans le document XML.
        current_book, current_chapter, current_verse_obj = None, None, None
        is_in_verse_scope = False  # Indique si cela se trouve entre un <verse sID> et <verse eID>.
        words_to_create = []       # Liste pour la création en masse des objets WordStrong.
        verse_text_parts = []      # Liste pour assembler le texte complet d'un verset.
        position_counter = 0       # Compteur pour la position des mots dans un verset.

        # Dictionnaire pour le récapitulatif final.
        counters = {
            'books': 0, 'chapters': 0, 'verses': 0, 'words': 0,
            'words_in_w': 0, 'text_fragments': 0
        }

        # Utilisation d' etree.iterparse pour parcourir le fichier de manière itérative,
        # ce qui est crucial pour ne pas charger tout le fichier (potentiellement volumineux) en mémoire.
        context = etree.iterparse(xml_file_path, events=('start', 'end'), remove_blank_text=True)

        for event, element in context:
            local_name = etree.QName(element.tag).localname

            # Logique exécutée au DÉBUT d'une balise
            if event == 'start':
                if local_name == 'div' and element.get('type') == 'book':
                    book_title_element = element.find(f'{{{OSIS_NS}}}title')
                    book_title = book_title_element.text.strip() if book_title_element is not None and book_title_element.text else "Titre Manquant"
                    osis_id = element.get('osisID', '').strip()
                    if osis_id:
                        try:
                            # Utilisation de la liste canonique pour définir l'ordre de tri.
                            order = CANONICAL_OSIS_ID_ORDER.index(osis_id) + 1
                        except ValueError:
                            order = 999
                            self.stdout.write(self.style.WARNING(f"Livre avec OSIS ID '{osis_id}' non trouvé dans l'ordre canonique."))
                        
                        current_book, created = Book.objects.get_or_create(
                            osis_id=osis_id,
                            defaults={'name': book_title, 'book_order': order}
                        )
                        if created: counters['books'] += 1

                elif local_name == 'chapter' and current_book:
                    osis_id_attr = element.get('osisID', '').strip()
                    num_str = osis_id_attr.split('.')[-1].strip()
                    if num_str.isdigit():
                        chapter_number = int(num_str)
                        current_chapter, created = Chapter.objects.get_or_create(book=current_book, chapter_number=chapter_number, defaults={'osis_id': osis_id_attr})
                        if created: counters['chapters'] += 1

                # Une balise <verse sID> marque le début d'un jalon de verset.
                # Activation du mode "collecte de texte".
                elif local_name == 'verse' and element.get('sID') and current_chapter:
                    is_in_verse_scope = True
                    words_to_create = []
                    verse_text_parts = [] # Réinitialiser pour chaque verset
                    position_counter = 0
                    
                    verse_n_str = element.get('n', '').strip()
                    if verse_n_str.isdigit():
                        verse_number = int(verse_n_str)
                        current_verse_obj, created = Verse.objects.get_or_create(
                            chapter=current_chapter, verse_number=verse_number,
                            defaults={'osis_id': element.get('osisID', '').strip(), 'text': '[en cours]'}
                        )
                        if created: counters['verses'] += 1
            
            # Logique exécutée à la FIN d'une balise
            elif event == 'end':
                # --- Logique de collecte de texte pour le verset complet ---
                # Si le mode "collecte" est actif et que la balise n'est pas un verset lui-même,
                # Capture du contenu (.text) et le texte qui la suit (.tail).
                # CLEF pour gérer la structure en jalons du fichier OSIS.
                if is_in_verse_scope and local_name != 'verse':
                    if element.text:
                        verse_text_parts.append(element.text)
                        if local_name != 'w':
                            counters['text_fragments'] += len(element.text.split())
                    if element.tail:
                        verse_text_parts.append(element.tail)
                        counters['text_fragments'] += len(element.tail.split())

                # --- Logique de tokenisation pour les numéros Strong ---
                # Cette partie est distincte et a pour but de créer des objets WordStrong
                # pour une analyse plus fine du texte.
                if is_in_verse_scope and current_verse_obj:
                    if local_name == 'w':
                        word_text = element.text.strip() if element.text else ''
                        if word_text:
                            counters['words_in_w'] += 1 # Compteur pour les mots dans <w>
                            position_counter += 1
                            strong_numbers = element.get('lemma', '').replace('strong:', '').split()
                            words_to_create.append(WordStrong(
                                verse=current_verse_obj, text=word_text, position=position_counter,
                                strong_ids=",".join(s.strip() for s in strong_numbers if s.strip()) or None
                            ))

                    if element.tail and element.tail.strip():
                        tokens = re.findall(r'[\w\'-]+|[.,;!?:]', element.tail.strip())
                        for token in tokens:
                            position_counter += 1
                            words_to_create.append(WordStrong(
                                verse=current_verse_obj, text=token, position=position_counter, strong_ids=None
                            ))

                # Une balise <verse eID> marque la fin d'un jalon de verset.
                # C'est ici qu'est assemblé le texte collecté et que le tout est sauvegardé.
                if local_name == 'verse' and element.get('eID') and current_verse_obj:
                    # Assemblage de tous les morceaux de texte collectés.
                    full_text = ''.join(verse_text_parts).strip()
                    # Normalisation des espaces multiples.
                    full_text = ' '.join(full_text.split()).strip()

                    # Ajout du numéro de verset au début du texte pour l'affichage.
                    full_text = f"[{current_verse_obj.verse_number}] {full_text}"

                    current_verse_obj.text = full_text
                    current_verse_obj.save(update_fields=['text'])

                    if words_to_create:
                        WordStrong.objects.bulk_create(words_to_create)
                        counters['words'] += len(words_to_create)

                    # Réinitialisation des variables d'état pour le prochain verset.
                    is_in_verse_scope = False
                    current_verse_obj = None
                    words_to_create = []
                    verse_text_parts = []

                # --- Gestion de la mémoire ---
                # Il est crucial de nettoyer les éléments de l'arbre XML au fur et à mesure
                # pour éviter que l'application ne sature la RAM.
                element.clear()
                while element.getprevious() is not None:
                    del element.getparent()[0]

        summary = (f"Récapitulatif : {counters['books']} livres, {counters['chapters']} chapitres, "
                   f"{counters['verses']} versets, {counters['words']} tokens forts.\n"
                   f"Détail du texte collecté : {counters['words_in_w']} mots balisés (<w>) et "
                   f"{counters['text_fragments']} fragments de texte hors balises.")
        self.stdout.write(self.style.SUCCESS(summary))