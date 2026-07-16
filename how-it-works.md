# how kontext works — the big picture

kontext turns a folder full of ebooks into something you can ask questions.
you type a thought — *"why do we remember smells so vividly?"* — and it shows
you the passages across tens of thousands of books that talk about exactly
that, each with the book, the author, and the page it came from.

no ai writes anything here. kontext only *finds*; the answers are the books
themselves.

it gets there in five steps. the first four are preparation and run once
(plus a little top-up whenever new books arrive). only the last one —
search — is what you use day to day.

## step 1: survey — take inventory

before touching anything, kontext walks the whole folder and takes stock:
what file formats are in there, which files are duplicates of each other,
which pdfs contain real text and which are just photographed pages, what
languages the books are in.

the result is a report card for the collection and a plan for everything
that follows. nothing is changed or moved — the original files are treated
as read-only, always.

## step 2: ingest — get the text out

a pdf, an epub and a djvu file all store text in completely different ways,
so kontext opens each book with a format-specific reader and pulls out the
raw text, chapter by chapter, page by page. along the way it records the
book's title, author and language, and notices when two files are really
the same work in different formats, so the library counts books, not files.

books that are only *pictures* of pages (scanned books) have no text to
extract yet. they go into a separate queue for ocr — optical character
recognition, software that reads the page images and types out what it
sees. ocr is by far the slowest part of the whole pipeline, so it runs on
the side, for as long as it takes, and each finished book simply joins the
index late. everything else doesn't wait for it.

## step 3: chunk — cut books into passages

nobody searches for a whole book; you search for a *place in* a book. so
every book is cut into overlapping passages of roughly 300 words — about a
long paragraph or two. the cuts respect sentences and never cross chapter
boundaries, and neighbouring passages overlap a little so that no idea gets
lost by being sliced through the middle.

crucially, every passage remembers where it came from: which book, which
page (for pdfs) or which chapter (for epubs). that little return address
is what makes search results point back into the physical book.

## step 4: embed — build the map of meaning

this is the step that makes the search *smart*, and the one that keeps the
gpu busy for days.

a neural network reads each passage and converts it into a list of 1,024
numbers. those numbers are coordinates — a location on a giant abstract
map where **passages that talk about similar things end up close
together**, even if they share no words at all. a passage about memory and
forgetting from a neuroscience textbook and one from a proust novel land
near each other; a passage about diesel engines lands far away from both.
the network was trained on hundreds of millions of text pairs precisely so
that "nearby on the map" means "about the same thing" — and it works
across languages, so a french passage sits next to its english translation.

each passage is placed on this map exactly once. that's why the step is
slow but the result is permanent: a new book later just means placing a few
hundred new points, done in seconds.

## step 5: search — ask the library a question

when you type a query, two searches run at the same time:

- **meaning search:** your query is converted into coordinates by the same
  network, and the map is asked: *which stored passages are closest?* this
  finds things that are about your idea, no matter how they phrase it.
- **word search:** a classic keyword index (like ctrl+f, but over
  everything) finds exact names, rare terms and precise phrases — the
  things meaning-search is fuzzy about.

the two result lists are merged, and a second, more careful neural network
re-reads the best ~50 candidates against your query and puts the truly
best ones on top. you get about ten passages, each labeled with book,
author, page or chapter, and a link to the original file.

## where results point

every hit tells you where it lives:

- **pdf / djvu:** real page numbers — "p. 217" or "pp. 217–218", so you can
  open the original file straight to the spot.
- **epub / mobi:** these formats have no fixed pages (text reflows with
  font size), so results say the chapter instead — plus the passage text
  itself, which you can find inside the book instantly.

and since the original files are never moved or altered, every result can
link directly to the untouched source file.

## why this design

- **adding books is cheap.** nothing is ever "retrained": a new book is
  extracted, chunked and placed on the map in seconds, and old books are
  unaffected.
- **everything is resumable.** every step remembers what's done, so any of
  them can be interrupted and picked up later — important when a step runs
  for days.
- **the originals are sacred.** kontext reads the dump, it never rewrites
  it. deleting kontext's databases loses nothing but time.
