from django.db import models
from django.db import connection


class ReferenceSource(models.OneToOneField):
    def __init__(self, *args, **kwargs):
        default_kwargs = {
            'db_index': True,
            'unique': True,
            'blank': True,
            'null': True
        }
        default_kwargs.update(kwargs)
        super(ReferenceSource, self).__init__(*args, **default_kwargs)

    @property
    def source_table_name(self):
        return self.related_model._meta.db_table

    @property
    def reference_model_table_name(self):
        return self.model._meta.db_table

    @property
    def trigger_name(self):
        return 'update_{}_{}_trigger'.format(
            self.source_table_name, self.reference_model_table_name
        )

    @property
    def index_name(self):
        return 'unique_{}_{}_ix'.format(
            self.source_table_name, self.reference_model_table_name
        )

    @property
    def trigger_function_name(self):
        return 'update_{}_{}_()'.format(
            self.source_table_name, self.reference_model_table_name
        )

    def get_proxy_and_foreign_cols(self):
        proxies = self.model.get_field_proxies()
        overrides = {
            proxy.reference_field: value
            for proxy in [p for p in proxies if p.foreign_fields]
                for field, value in proxy.foreign_fields.iteritems()
                    if field == self
        }  # nopep8
        proxy_field_cols = []
        foreign_cols = []

        for proxy in proxies:
            proxy_field_col = proxy.foreign_column
            foreign_col = overrides.get(proxy.reference_field, proxy_field_col)
            if foreign_col is not None:
                if not isinstance(foreign_col, models.expressions.Combinable):
                    foreign_expression = models.F(foreign_col)
                else:
                    foreign_expression = foreign_col

                # Generate a qs, and grab the annotation from it.
                qs = self.related_model.objects.annotate(
                    ann_forex=foreign_expression
                )
                ann = qs.query.annotations['ann_forex']

                if (
                    len(qs.query.tables) > 1 or
                    self.source_table_name not in qs.query.tables
                ):
                    raise Exception(
                        'Proxy expressions can only use 1 table'
                    )

                # The rendered SQL refers to the tablename when we want
                # it to refer to NEW
                compiler = qs.query.get_compiler(using='default')
                resolved = ann.as_sql(compiler, connection)
                replaced = resolved[0].replace(
                    '\"{}\"'.format(self.source_table_name), 'NEW'
                )

                proxy_field_cols.append(proxy_field_col)
                foreign_cols.append(
                    (replaced, resolved[1], ann)
                )
        return proxy_field_cols, foreign_cols

    @property
    def trigger_function_statement(self):

        proxy_field_cols, foreign_cols = self.get_proxy_and_foreign_cols()

        fk_col_name = self.related_model._meta.pk.column
        insert_col_names = [self.column] + proxy_field_cols
        insert_value_cols = \
            [('NEW.{}'.format(fk_col_name), [], None)] + foreign_cols

        into_statement = (
            '{ref_model_table}({comma_sep_cols})'.format(
                ref_model_table=self.reference_model_table_name,
                comma_sep_cols=', '.join([col for col in insert_col_names])
            )

        )

        values_statement = (
            '({comma_sep_cols})'.format(
                comma_sep_cols=', '.join(
                    [col for col, _, _ in insert_value_cols]
                )
            )
        )

        set_statement = (
            '({comma_sep_proxy}) = '
            '({comma_sep_foreign})'.format(
                comma_sep_proxy=', '.join(
                    [col for col in proxy_field_cols]
                ),
                comma_sep_foreign=', '.join(
                    [col for col, _, _ in foreign_cols]
                )
            )
        )
        return (
            'CREATE OR REPLACE FUNCTION {trigger_func_name} RETURNS trigger as '
                '$$ '
                    'BEGIN '
                        'BEGIN '
                            'INSERT INTO {into_statement} '
                                'VALUES {values_statement}'
                            '; '
                        'EXCEPTION WHEN unique_violation THEN '
                            'UPDATE {reference_model_table_name}'
                                ' SET {set_statement}'
                                ' WHERE {column} = NEW.{fk_col_name}'
                            '; '
                        'END; '
                    'RETURN NEW; '
                    'END '
                '$$ LANGUAGE plpgsql'
            ';'.format(
                trigger_func_name=self.trigger_function_name,
                into_statement=into_statement,
                values_statement=values_statement,
                reference_model_table_name=self.reference_model_table_name,
                set_statement=set_statement,
                column=self.column,
                fk_col_name=fk_col_name
            ),
            [
                param
                for group in [insert_value_cols, foreign_cols]
                    for _, params, _ in group
                        for param in params
            ]
        )  # nopep8

    @property
    def drop_trigger_statement(self):
        return (
            'DROP TRIGGER IF EXISTS {trigger_name} '
            'ON {table_name}'
            ';'.format(
                trigger_name=self.trigger_name,
                table_name=self.source_table_name
            ),
            []
        )

    @property
    def create_trigger_statement(self):
        _, foreign_cols = self.get_proxy_and_foreign_cols()
        trigger_cols = {
            col.field.column
            for _, _, exp in foreign_cols
            for col in [c for c in exp.flatten() if isinstance(c, Col)]
        }

        comma_sep_trigger_cols = ', '.join([col for col in trigger_cols])

        return (
            'CREATE TRIGGER {trigger_name} AFTER INSERT '
                'OR UPDATE OF {trigger_cols} '
                    'ON {table_name} '
                    'FOR EACH ROW EXECUTE PROCEDURE {trigger_func_name}'
            ';'.format(
                trigger_name=self.trigger_name,
                table_name=self.source_table_name,
                trigger_cols=comma_sep_trigger_cols,
                trigger_func_name=self.trigger_function_name
            ),
            []
        )  # nopep8

    @property
    def index_statement(self):
        return (
            'DO $$ '
                'BEGIN {index_function_statement}'
                    'EXCEPTION WHEN duplicate_table THEN '
                       'DROP INDEX {index_name};'
                       '{index_function_statement}'
                'END; '
            '$$;'.format(
                    index_function_statement=self.index_function_statement,
                    index_name=self.index_name
                ),
            []
        )  # nopep8

    @property
    def index_function_statement(self):
        return (
            'CREATE UNIQUE INDEX ' + self.index_name + ' ON ' +
            self.reference_model_table_name + '(' + self.column + ');'
        )


class AbstractProxy(object):
    """
    Django doesn't pick up field changes if you have more than one __init__
    statement, so we have to have this hacky workaround.
    """
    def _run_init(self,
                  reference_field,
                  foreign_field=None,
                  foreign_fields=None):
        self.reference_field = reference_field
        self.foreign_fields = foreign_fields
        self.foreign_field = foreign_field

    @property
    def foreign_column(self):
        return self.foreign_field or self.reference_field.column


class FieldProxy(AbstractProxy):
    def __init__(self, *args, **kwargs):
        self._run_init(*args, **kwargs)


class ProxiedField(AbstractProxy):

    def _run_field_init(self, args, kwargs):

        foreign_fields = kwargs.pop('foreign_fields', None)
        foreign_field = kwargs.pop('foreign_field', None)

        self._run_init(
            self, foreign_field=foreign_field,
            foreign_fields=foreign_fields
        )
        kwargs['db_index'] = True
        defaults = {
            'db_index': True,
            'blank': True,
            'null': True
        }
        defaults.update(kwargs)
        # We took out some kwargs, so return the clean dict
        return args, defaults


class ProxiedDatetimeField(ProxiedField, models.DateTimeField):
    def __init__(self, *args, **kwargs):
        args, kwargs = self._run_field_init(args, kwargs)
        super(ProxiedDatetimeField, self).__init__(*args, **kwargs)


class ProxiedDateField(ProxiedField, models.DateField):
    def __init__(self, *args, **kwargs):
        args, kwargs = self._run_field_init(args, kwargs)
        super(ProxiedDateField, self).__init__(*args, **kwargs)


class ProxiedIntegerField(ProxiedField, models.IntegerField):
    def __init__(self, *args, **kwargs):
        args, kwargs = self._run_field_init(args, kwargs)
        super(ProxiedIntegerField, self).__init__(*args, **kwargs)


class ProxiedForeignKey(ProxiedField, models.ForeignKey):
    def __init__(self, *args, **kwargs):
        args, kwargs = self._run_field_init(args, kwargs)
        super(ProxiedForeignKey, self).__init__(*args, **kwargs)


class ProxiedTextField(ProxiedField, models.TextField):
    def __init__(self, *args, **kwargs):
        args, kwargs = self._run_field_init(args, kwargs)
        super(ProxiedTextField, self).__init__(*args, **kwargs)


class ProxiedCharField(ProxiedField, models.CharField):
    def __init__(self, *args, **kwargs):
        args, kwargs = self._run_field_init(args, kwargs)
        super(ProxiedCharField, self).__init__(*args, **kwargs)


class ReferenceQuerySet(models.QuerySet):

    def prepare_unpack(self):
        source_names = [
            source.name
            for source in self.model.get_reference_sources()
        ]
        return self.select_related(*source_names)

    def unpack(self):
        return [
            entry.unpack() for entry in self.prepare_unpack()
        ]

    def iter_unpack(self):
        for entry in self.prepare_unpack().iterator():
            yield entry.unpack()

    def select_sources(self, *sources):
        if not len(sources):
            raise Exception('At least one source needs to be specified')
        q = models.Q()
        for source in self.model.get_reference_sources():
            if source.attname in sources or source.name in sources:
                q |= models.Q(**{'{}__isnull'.format(source.attname): False})
        return self.filter(
            q
        )

    def delete(self, using=None):
        raise Exception('Cannot delete a Reference')

    def update(self, *args, **kwargs):
        raise Exception('Only triggers can update References')


class ReferenceManager(models.Manager):
    queryset_class = ReferenceQuerySet

    def get_queryset(self):
        return self.queryset_class(self.model, using=self._db)


class ReferenceModel(models.Model):

    objects = ReferenceManager()

    class Meta:
        abstract = True

    @classmethod
    def get_field_proxies(cls):
        class_attrs = cls.__dict__.values()
        fields = cls._meta.get_fields()
        class_attrs.extend(fields)
        return [
            f for f in class_attrs
            if isinstance(f, AbstractProxy)
        ]

    @classmethod
    def get_reference_sources(cls):
        fields = cls._meta.get_fields()
        return [
            f for f in fields
            if isinstance(f, ReferenceSource)
        ]

    @classmethod
    def make_add_constaint_statement(cls):
        return (
            'ALTER TABLE {db_table} ADD CONSTRAINT {constraint_name} '
            '{constraint_check}'
            ';'.format(
                db_table=cls._meta.db_table,
                constraint_name=cls.make_constraint_name(),
                constraint_check=cls.make_constraint_check()
            ),
            []
        )

    @classmethod
    def make_drop_constraint_statement(cls):
        return (
            'ALTER TABLE {db_table} DROP CONSTRAINT '
            'IF EXISTS {constraint_name}'
            ';'.format(
                db_table=cls._meta.db_table,
                constraint_name=cls.make_constraint_name()
            ),
            []
        )

    @classmethod
    def make_constraint_name(cls):
        return 'constraint_{}_only_one_source'.format(cls._meta.db_table)

    @classmethod
    def make_constraint_check(cls):
        return (
            ' CHECK ('
                '({check}) = 1'
            ')'.format(
                check=' + '.join([
                    'CASE WHEN {}'
                    ' IS NOT NULL THEN 1 ELSE 0 END'.format(source.column)
                    for source in cls.get_reference_sources()
                ])
            )
        )   # nopep8

    @classmethod
    def _gen_trigger_statements(cls):
        return (
            statement
            for reference in cls.get_reference_sources()
                for statement in (
                    reference.trigger_function_statement,
                    reference.drop_trigger_statement,
                    reference.create_trigger_statement
                )
        )  # nopep8

    @classmethod
    def _gen_index_statements(cls):
        return (
            reference.index_statement
            for reference in cls.get_reference_sources()
        )

    @classmethod
    def _gen_constraint_statements(cls):
        return (
            x for x in (
                cls.make_drop_constraint_statement(),
                cls.make_add_constaint_statement()
            )
        )

    @classmethod
    def _gen_all_statements(cls):
        return (
            x for group in (
                cls._gen_constraint_statements(),
                cls._gen_index_statements(),
                cls._gen_trigger_statements()
            )
            for x in group
        )

    @classmethod
    def _run_trigger_statements(cls):
        cls._execute_sql(cls._gen_trigger_statements())

    @classmethod
    def _run_index_statements(cls):
        cls._execute_sql(cls._gen_index_statements())

    @classmethod
    def _run_constraint_statements(cls):
        cls._execute_sql(cls._gen_constraint_statements())

    @classmethod
    def _run_sql_statements(cls):
        cls._execute_sql(cls._gen_all_statements())

    @classmethod
    def _execute_sql(cls, iterable):
        with connection.cursor() as cursor:
            for statement, params in iterable:
                cursor.execute(statement, params)

    def unpack(self):
        for source in self.get_reference_sources():
            if getattr(self, source.column) is not None:
                return getattr(self, source.name)

        raise Exception('Reference has no source')

    def delete(self, using=None):
        raise Exception('Cannot delete a Reference')

    def save(self, *args, **kwargs):
        raise Exception('Only triggers can update References')

