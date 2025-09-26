OSIS XML Importer for Django
This directory contains a Django management command designed to parse a Bible OSIS XML file and import its content into a database. It is engineered to be highly efficient and robust, making it suitable for processing large, complex XML files like Sg1910_v11n.osis.xml.

This document is divided into two parts:

Script Architecture: An in-depth explanation of how the script works.

Setup and Usage: A practical guide to integrate and run the script in a Django project.

1. Script Architecture & Logic
Understanding the script's design is key to appreciating its efficiency and adapting it if needed.

Core Philosophy: Memory Efficiency
The primary challenge with large XML files (often >10MB) is memory consumption. Loading the entire file into a DOM tree can easily exhaust a server's RAM. This script avoids that problem by using iterative parsing via the lxml.etree.iterparse library.

Instead of building a full tree, iterparse reads the XML file sequentially, emitting events (like the start or end of an element) as it goes. This allows us to process the document piece by piece and then discard elements from memory immediately after they've been processed, resulting in a very low and constant memory footprint.

Handling the "Milestone" Verse Structure
A critical feature of the Sg1910_v11n.osis.xml file is that verses are not contained within a single <verse>...</verse> block. Instead, their boundaries are marked by empty "milestone" tags:

<verse sID="..." n="1" /> marks the start of verse 1.

<verse eID="..." n="1" /> marks the end of verse 1.

The text and word elements for that verse appear between these two tags. The script manages this with a state variable, is_in_verse_scope, which acts like a switch:

When an sID (start) tag is encountered, is_in_verse_scope is set to True. The script now knows it should start collecting all subsequent text.

When an eID (end) tag is found, the collected verse text is saved to the database, and is_in_verse_scope is set back to False.

Dual Data Collection Strategy
While inside a verse's scope, the script collects data in two parallel ways:

Full Verse Text (verse_text_parts list): This list gathers every piece of text (element.text) and tailing text (element.tail) encountered. At the end of the verse (eID), these fragments are joined together to form the complete, human-readable verse text.

Tokenized Words for Analysis (words_to_create list): This list is for a more granular analysis. It stores WordStrong objects for each individual word or punctuation mark.

If a word is inside a <w lemma="strong:..."> tag, its text and associated Strong's number(s) are captured.

If text appears outside a <w> tag (e.g., plain text or punctuation), it is tokenized and stored as a WordStrong object without a Strong's ID.
This preserves the exact sequence of every element in the verse, which is crucial for detailed textual study.

Database Integrity and Performance
Atomic Transactions: The entire import process is wrapped in transaction.atomic(). If any error occurs midway through, all database changes are rolled back, preventing a partially imported and corrupted dataset.

Idempotency: Before starting, the script deletes all existing biblical data. This makes the command idempotent: you can run it multiple times and always get the same clean, final state without creating duplicates.

Bulk Creation: Instead of saving each WordStrong object to the database one by one (which is very slow), they are collected in the words_to_create list. Once a verse is fully parsed, WordStrong.objects.bulk_create() inserts all of them in a single, efficient database query.

2. Setup and Usage Guide
Follow these steps to integrate and use the importer in your Django project.

Requirements
Python 3.x

Django >= 3.2

lxml

Step 1: Place the Script
Copy the import_osis_en.py file into one of your Django app's management command directories. It's recommended to rename it to import_osis.py.

Path: <your_app>/management/commands/import_osis.py

If the management and commands directories don't exist, you'll need to create them. Remember to add an empty __init__.py file in each.

Step 2: Define Django Models
Ensure you have Django models that match the script's data structure. You can use the following example in your app's models.py file.

File: <your_app>/models.py

# In your_app/models.py
from django.db import models

class Book(models.Model):
    name = models.CharField(max_length=100)
    osis_id = models.CharField(max_length=10, unique=True)
    book_order = models.IntegerField(unique=True)

    def __str__(self):
        return self.name

class Chapter(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='chapters')
    chapter_number = models.IntegerField()
    osis_id = models.CharField(max_length=20, unique=True)

    class Meta:
        unique_together = ('book', 'chapter_number')

    def __str__(self):
        return f"{self.book.name} {self.chapter_number}"

class Verse(models.Model):
    chapter = models.ForeignKey(Chapter, on_delete=models.CASCADE, related_name='verses')
    verse_number = models.IntegerField()
    text = models.TextField()
    osis_id = models.CharField(max_length=30, unique=True)

    class Meta:
        unique_together = ('chapter', 'verse_number')

    def __str__(self):
        return f"{self.chapter} : {self.verse_number}"

class WordStrong(models.Model):
    verse = models.ForeignKey(Verse, on_delete=models.CASCADE, related_name='words')
    text = models.CharField(max_length=100)
    position = models.IntegerField()
    strong_ids = models.CharField(max_length=255, null=True, blank=True)

    def __str__(self):
        return f"({self.position}) {self.text} in {self.verse}"

Step 3: Position the XML File
The script looks for the XML file at <project_root>/osis_origine/Sg1910_v11n.osis.xml. Create the osis_origine directory in your project's root and place the XML file inside it, or update the xml_file_path variable in the script to the correct location.

Step 4: Install Dependencies
Install the lxml library using pip:

pip install lxml

Step 5: Apply Database Migrations
Generate and run the migrations to create the necessary tables in your database:

python manage.py makemigrations
python manage.py migrate

Step 6: Run the Importer
Execute the command from your project's root directory. The process may take a few minutes to complete.

python manage.py import_osis

Upon successful completion, you will see a summary message, and your database will be populated with the structured biblical data.