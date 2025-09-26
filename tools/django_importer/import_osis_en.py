# -*- coding: utf-8 -*-

"""
Django management command to import a Bible file in OSIS XML format
into the database.

This script is designed to be robust and memory-efficient by using
iterative parsing (iterparse) of the XML file. It handles the specific
structure of the Sg1910_v11n.osis.xml file, which uses milestone tags
to delimit verses.
"""

import os
import re
from lxml import etree
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings

from preacher_helper.models import Book, Chapter, Verse, WordStrong

# XML namespace for the OSIS format.
OSIS_NS = "http://www.bibletechnologies.net/2003/OSIS/namespace"

# List of OSIS book IDs in their canonical order.
# This list is crucial to ensure that books are sorted correctly
# in the application, and not alphabetically.
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
    help = 'Robustly imports an OSIS XML file.'
    def handle(self, *args, **options):
        xml_file_path = os.path.join(settings.BASE_DIR, 'osis_origine', 'Sg1910_v11n.osis.xml')
        
        if not os.path.exists(xml_file_path):
            raise CommandError(f"XML file not found: {xml_file_path}")

        self.stdout.write(self.style.SUCCESS("Starting robust import..."))

        try:
            # The atomic transaction ensures that if an error occurs,
            # the database is rolled back to its previous state.
            with transaction.atomic():
                self.run_import(xml_file_path)
        except Exception as e:
            import traceback
            traceback.print_exc()
            raise CommandError(f"A fatal error occurred: {e}")

        self.stdout.write(self.style.SUCCESS("\nImport finished successfully!"))

    def run_import(self, xml_file_path):
        # The script is idempotent: it cleans up old data before each import.
        self.stdout.write("Cleaning up old data...")
        WordStrong.objects.all().delete()
        Verse.objects.all().delete()
        Chapter.objects.all().delete()
        Book.objects.all().delete()
        self.stdout.write("Old data deleted.")

        # State variables to track the position in the XML document.
        current_book, current_chapter, current_verse_obj = None, None, None
        is_in_verse_scope = False  # Flag to indicate if we are between a <verse sID> and <verse eID>.
        words_to_create = []       # List for bulk creation of WordStrong objects.
        verse_text_parts = []      # List to assemble the full text of a verse.
        position_counter = 0       # Counter for word position within a verse.

        # Dictionary for the final summary.
        counters = {
            'books': 0, 'chapters': 0, 'verses': 0, 'words': 0,
            'words_in_w': 0, 'text_fragments': 0
        }

        # Using etree.iterparse to process the file iteratively,
        # which is crucial to avoid loading the entire (potentially large) file into memory.
        context = etree.iterparse(xml_file_path, events=('start', 'end'), remove_blank_text=True)

        for event, element in context:
            local_name = etree.QName(element.tag).localname

            # Logic executed at the START of a tag
            if event == 'start':
                if local_name == 'div' and element.get('type') == 'book':
                    book_title_element = element.find(f'{{{OSIS_NS}}}title')
                    book_title = book_title_element.text.strip() if book_title_element is not None and book_title_element.text else "Missing Title"
                    osis_id = element.get('osisID', '').strip()
                    if osis_id:
                        try:
                            # Using the canonical list to define the sort order.
                            order = CANONICAL_OSIS_ID_ORDER.index(osis_id) + 1
                        except ValueError:
                            order = 999
                            self.stdout.write(self.style.WARNING(f"Book with OSIS ID '{osis_id}' not found in canonical order."))
                        
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

                # A <verse sID> tag marks the beginning of a verse milestone.
                # Activating the "text collection" mode.
                elif local_name == 'verse' and element.get('sID') and current_chapter:
                    is_in_verse_scope = True
                    words_to_create = []
                    verse_text_parts = [] # Reset for each verse
                    position_counter = 0
                    
                    verse_n_str = element.get('n', '').strip()
                    if verse_n_str.isdigit():
                        verse_number = int(verse_n_str)
                        current_verse_obj, created = Verse.objects.get_or_create(
                            chapter=current_chapter, verse_number=verse_number,
                            defaults={'osis_id': element.get('osisID', '').strip(), 'text': '[in progress]'}
                        )
                        if created: counters['verses'] += 1
            
            # Logic executed at the END of a tag
            elif event == 'end':
                # --- Text collection logic for the full verse ---
                # If collection mode is active and the tag is not a verse itself,
                # capture its content (.text) and the text that follows it (.tail).
                # KEY to handling the milestone structure of the OSIS file.
                if is_in_verse_scope and local_name != 'verse':
                    if element.text:
                        verse_text_parts.append(element.text)
                        if local_name != 'w':
                            counters['text_fragments'] += len(element.text.split())
                    if element.tail:
                        verse_text_parts.append(element.tail)
                        counters['text_fragments'] += len(element.tail.split())

                # --- Tokenization logic for Strong's numbers ---
                # This part is separate and aims to create WordStrong objects
                # for a more detailed analysis of the text.
                if is_in_verse_scope and current_verse_obj:
                    if local_name == 'w':
                        word_text = element.text.strip() if element.text else ''
                        if word_text:
                            counters['words_in_w'] += 1 # Counter for words within <w>
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

                # A <verse eID> tag marks the end of a verse milestone.
                # This is where the collected text is assembled and everything is saved.
                if local_name == 'verse' and element.get('eID') and current_verse_obj:
                    # Assembling all the collected text fragments.
                    full_text = ''.join(verse_text_parts).strip()
                    # Normalizing multiple spaces.
                    full_text = ' '.join(full_text.split()).strip()

                    # Adding the verse number at the beginning of the text for display.
                    full_text = f"[{current_verse_obj.verse_number}] {full_text}"

                    current_verse_obj.text = full_text
                    current_verse_obj.save(update_fields=['text'])

                    if words_to_create:
                        WordStrong.objects.bulk_create(words_to_create)
                        counters['words'] += len(words_to_create)

                    # Resetting state variables for the next verse.
                    is_in_verse_scope = False
                    current_verse_obj = None
                    words_to_create = []
                    verse_text_parts = []

                # --- Memory management ---
                # It is crucial to clean up XML tree elements as we go
                # to prevent the application from saturating RAM.
                element.clear()
                while element.getprevious() is not None:
                    del element.getparent()[0]

        summary = (f"Summary: {counters['books']} books, {counters['chapters']} chapters, "
                   f"{counters['verses']} verses, {counters['words']} strong tokens.\n"
                   f"Collected text details: {counters['words_in_w']} tagged words (<w>) and "
                   f"{counters['text_fragments']} text fragments outside tags.")
        self.stdout.write(self.style.SUCCESS(summary))