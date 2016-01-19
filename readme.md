# Django Polymorphic Queries

Polymorphic queries allow you to run 1 query across multiple SQL tables.
This is accomplished by creating a ReferenceModel which acts as a common
index to the other tables.

To query polymorphic values, you first need a ReferenceModel. The model
is a normal django model of ReferenceSources (foreign keys) to the other
tables and FieldProxies which precompute values or denormalize the
ReferenceSource's fields. This happens through Postgres triggers so there will
be no additional calls made by the ORM. Having denormalized precomputed data
makes queries across multiple tables more efficient than concatenating
sequential single table queries.

When compared to looping over the other models and doing individual queries,
loading them into python memory to sort and splice, using ReferenceModels
is faster, saves db reads and skips the annoyance of iterating over models.

Triggers are added to Postgres by the ReferenceModel. Triggers allow you to
intercept events in postgres and run a function, which in our case means
updating the ReferenceModel table. This happens on the database level
during INSERT and UPDATE operations which means that your model
should always be up to date.


# Example:

```
from polymorphic.models import ReferenceModel


class Movie(models.Model):
    title = models.TextField()
    director = models.TextField(db_index=True)
    producer = models.TextField(db_index=True)
    runtime = models.IntegerField(db_index=True)
    release_date = models.DateField(db_index=True)


class Book(models.Model):
    title = models.TextField(db_index=True)
    author = models.TextField(db_index=True)
    num_pages = models.IntegerField(db_index=True)
    create_date = models.DateField(db_index=True)


class Song(models.Model):
    title = models.TextField(db_index=True)
    artist = models.TextField(db_index=True)
    producer = models.TextField(db_index=True)
    album = models.TextField(db_index=True)
    runtime = models.IntegerField(db_index=True)
    create_date = models.DateField(db_index=True)


class MediaReference(ReferenceModel):

    # ReferenceSources point to the tables you want this model to track.
    # A ReferenceModel will only ever have one ReferenceSource column filled.
    # Unique indexes are created for each source.
    song = ReferenceSource(Song)
    book = ReferenceSource(Book)
    movie = ReferenceSource(Movie)

    # A proxied field  works by mirroring the ReferenceSources's column
    # in the ReferenceModel's table.
    # On the backend, postgres triggers update the index
    # whenever the field changes.
    # For example, if this was a Reference entry for a Song, this
    # ReferenceModel's title field would be populated with the title of
    # that Song.

    # Proxies are indexed, null, and blank by default.
    title = ProxiedTextField()

    # You can define what columns you want to use for each reference.
    # If you partially define the fields, it will fall back to the default.
    creator = ProxiedTextField(foreign_fields={
        song: 'artist',
        book: 'author',
        movie: 'director'
    })

    # Specifying the reference as None means it will not be set.
    producer = ProxiedTextField(foreign_fields={
        book: None
    })

    # If you don't want to use the Proxied* shortcut, there is `FieldProxy`
    # which accomplishes the same thing.
    create_date = models.DateField(db_index=True)
    create_date_proxy = FieldProxy(
        create_date, foreign_fields={
            movie: 'release_date'
        }
    )

    # ProxyFields accept Django Expressions which will precompute the column.
    # Postgres will evaluate the expression on update and insert. This means
    # the computation will only happen on writes.
    title_length = ProxiedTextField(foreign_field=Length('title'))

    # The expressions can be complex and apply to different sources.
    # They cannot reference other tables or do joins.
    weighted_length = ProxiedTextField(foreign_fields={
        song: F('runtime') * 30,
        book: F('num_pages') * Length('title'),
        movie: 'runtime'
    })
```


Querying MediaReferences act like any other Model

```
MediaReference.objects.filter(title__icontains='blah')
```

If you want to unpack all the references to a list (e.g. a list of Movie, Book, and Song objects), call "unpack"

```
MediaReference.objects.filter(creator__icontains='john').unpack()
```

With a performance hit, you can also query using joins

```
MediaReference.objects.filter(
    Q(song__runtime__gt=1000) | Q(book__num_pages__gt=500)
).unpack()
```


Selectively choose which tables to pull from...

```
MediaReference.objects.filter(
    create_date__lt=datetime.datetime(2015, 9, 1)
).limit_sources('movie', 'book')
```

ReferenceModels use custom queryset and managers, so if you would like to override the default manager or queryset, inherit from ReferenceManager or ReferenceQuerySet.