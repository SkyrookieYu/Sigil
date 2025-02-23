try:
    from collections.abc import Callable # Python 3.6
except ImportError as e:
    from collections import Callable
import sys
from collections import OrderedDict

from pdb import set_trace
import re
import warnings
from sigil_bs4.dammit import EntitySubstitution

DEFAULT_OUTPUT_ENCODING = "utf-8"

whitespace_re = re.compile("\s+")

NON_BREAKING_INLINE_TAGS = ("a","abbr","acronym","b","bdo","big","br",
    "button","cite","code","del","dfn","em","font","i","image","img",
    "input","ins","kbd","label","map","mark", "nobr","object","q",
    "ruby","rt","s","samp","select","small","span","strike","strong",
    "sub","sup","textarea","tt","u","var","wbr","mbp:nu")

PRESERVE_WHITESPACE_TAGS = ("code","pre","textarea","script","style")

VOID_TAGS = ("area","base","basefont","bgsound","br","col","command",
    "embed","event-source","frame","hr","img","input","keygen",
    "link","meta","param","source","spacer","track","wbr",
    "mbp:pagebreak")

NO_ENTITY_SUB_TAGS = ("script", "style")

SPECIAL_HANDLING_TAGS = ("html", "body")

STRUCTURAL_TAGS = ("article","aside","blockquote","body","canvas",
    "colgroup","div","dl","figure","footer","head","header","hr","html",
    "ol","section","table","tbody","tfoot","thead","td","th","tr","ul")

OTHER_TEXTHOLDING_TAGS = ("address","caption","dd","div","dt","figcaption","h1","h2",
    "h3","h4","h5","h6","legend","li","option","p","td","th","title")

EBOOK_XML_PARENT_TAGS = ("package","metadata","manifest","spine","guide","ncx",
                         "head","doctitle","docauthor","navmap", "navpoint",
                          "navlabel", "pagelist", "pagetarget") 

def _alias(attr):
    """Alias one attribute name to another for backward compatibility"""
    @property
    def alias(self):
        return getattr(self, attr)

    @alias.setter
    def alias(self):
        return setattr(self, attr)
    return alias


class NamespacedAttribute(str):

    def __new__(cls, prefix, name, namespace=None):
        if name is None:
            obj = str.__new__(cls, prefix)

        elif prefix is None:
            # Not really namespaced.
            obj = str.__new__(cls, name)
        else:
            obj = str.__new__(cls, prefix + ":" + name)
        obj.prefix = prefix
        obj.name = name
        obj.namespace = namespace
        return obj

class AttributeValueWithCharsetSubstitution(str):
    """A stand-in object for a character encoding specified in HTML."""

class CharsetMetaAttributeValue(AttributeValueWithCharsetSubstitution):
    """A generic stand-in for the value of a meta tag's 'charset' attribute.

    When Beautiful Soup parses the markup '<meta charset="utf8">', the
    value of the 'charset' attribute will be one of these objects.
    """

    def __new__(cls, original_value):
        obj = str.__new__(cls, original_value)
        obj.original_value = original_value
        return obj

    def encode(self, encoding):
        return encoding


class ContentMetaAttributeValue(AttributeValueWithCharsetSubstitution):
    """A generic stand-in for the value of a meta tag's 'content' attribute.

    When Beautiful Soup parses the markup:
     <meta http-equiv="content-type" content="text/html; charset=utf8">

    The value of the 'content' attribute will be one of these objects.
    """

    CHARSET_RE = re.compile("((^|;)\s*charset=)([^;]*)", re.M)

    def __new__(cls, original_value):
        match = cls.CHARSET_RE.search(original_value)
        if match is None:
            # No substitution necessary.
            return str.__new__(str, original_value)

        obj = str.__new__(cls, original_value)
        obj.original_value = original_value
        return obj

    def encode(self, encoding):
        def rewrite(match):
            return match.group(1) + encoding
        return self.CHARSET_RE.sub(rewrite, self.original_value)

class HTMLAwareEntitySubstitution(EntitySubstitution):

    """Entity substitution rules that are aware of some HTML quirks.

    Specifically, the contents of <script> and <style> tags should not
    undergo entity substitution.

    Incoming NavigableString objects are checked to see if they're the
    direct children of a <script> or <style> tag.
    """

    cdata_containing_tags = set(["script", "style"])

    preformatted_tags = set(["pre"])

    @classmethod
    def _substitute_if_appropriate(cls, ns, f):
        if (isinstance(ns, NavigableString)
            and ns.parent is not None
            and ns.parent.name in cls.cdata_containing_tags):
            # Do nothing.
            return ns
        # Substitute.
        return f(ns)

    @classmethod
    def substitute_html(cls, ns):
        return cls._substitute_if_appropriate(
            ns, EntitySubstitution.substitute_html)

    @classmethod
    def substitute_xml(cls, ns):
        return cls._substitute_if_appropriate(
            ns, EntitySubstitution.substitute_xml_containing_entities)

class PageElement(object):
    """Contains the navigational information for some part of the page
    (either a tag or a piece of text)"""

    # There are five possible values for the "formatter" argument passed in
    # to methods like encode() and prettify():
    #
    # "html" - All Unicode characters with corresponding HTML entities
    #   are converted to those entities on output.
    # "minimal" - Bare ampersands and angle brackets are converted to
    #   XML entities: &amp; &lt; &gt;
    # None - The null formatter. Unicode characters are never
    #   converted to entities.  This is not recommended, but it's
    #   faster than "minimal".
    # A function - This function will be called on every string that
    #  needs to undergo entity substitution.
    #

    # In an HTML document, the default "html" and "minimal" functions
    # will leave the contents of <script> and <style> tags alone. For
    # an XML document, all tags will be given the same treatment.

    HTML_FORMATTERS = {
        "html" : HTMLAwareEntitySubstitution.substitute_html,
        "minimal" : HTMLAwareEntitySubstitution.substitute_xml,
        None : None
        }

    XML_FORMATTERS = {
        "html" : EntitySubstitution.substitute_html,
        "minimal" : EntitySubstitution.substitute_xml_containing_entities,
        None : None
        }

    def format_string(self, s, formatter='minimal'):
        """Format the given string using the given formatter."""
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)
        if formatter is None:
            output = s
        else:
            output = formatter(s)
        return output

    @property
    def _is_xml(self):
        """Is this element part of an XML tree or an HTML tree?

        This is used when mapping a formatter name ("minimal") to an
        appropriate function (one that performs entity-substitution on
        the contents of <script> and <style> tags, or not). It's
        inefficient, but it should be called very rarely.
        """
        if self.parent is None:
            # This is the top-level object. It should have .is_xml set
            # from tree creation. If not, take a guess--BS is usually
            # used on HTML markup.
            return getattr(self, 'is_xml', False)
        return self.parent._is_xml

    def _formatter_for_name(self, name):
        "Look up a formatter function based on its name and the tree."
        if self._is_xml:
            return self.XML_FORMATTERS.get(
                name, EntitySubstitution.substitute_xml)
        else:
            return self.HTML_FORMATTERS.get(
                name, HTMLAwareEntitySubstitution.substitute_xml)

    def setup(self, parent=None, previous_element=None, next_element=None,
              previous_sibling=None, next_sibling=None):
        """Sets up the initial relations between this element and
        other elements."""
        self.parent = parent

        self.previous_element = previous_element
        if previous_element is not None:
            self.previous_element.next_element = self

        self.next_element = next_element
        if self.next_element:
            self.next_element.previous_element = self

        self.next_sibling = next_sibling
        if self.next_sibling:
            self.next_sibling.previous_sibling = self

        if (not previous_sibling
            and self.parent is not None and self.parent.contents):
            previous_sibling = self.parent.contents[-1]

        self.previous_sibling = previous_sibling
        if previous_sibling:
            self.previous_sibling.next_sibling = self

    nextSibling = _alias("next_sibling")  # BS3
    previousSibling = _alias("previous_sibling")  # BS3

    def replace_with(self, replace_with):
        if not self.parent:
            raise ValueError(
                "Cannot replace one element with another when the"
                "element to be replaced is not part of a tree.")
        if replace_with is self:
            return
        if replace_with is self.parent:
            raise ValueError("Cannot replace a Tag with its parent.")
        old_parent = self.parent
        my_index = self.parent.index(self)
        self.extract()
        old_parent.insert(my_index, replace_with)
        return self
    replaceWith = replace_with  # BS3

    def unwrap(self):
        my_parent = self.parent
        if not self.parent:
            raise ValueError(
                "Cannot replace an element with its contents when that"
                "element is not part of a tree.")
        my_index = self.parent.index(self)
        self.extract()
        for child in reversed(self.contents[:]):
            my_parent.insert(my_index, child)
        return self
    replace_with_children = unwrap
    replaceWithChildren = unwrap  # BS3

    def wrap(self, wrap_inside):
        me = self.replace_with(wrap_inside)
        wrap_inside.append(me)
        return wrap_inside

    def extract(self):
        """Destructively rips this element out of the tree."""
        if self.parent is not None:
            del self.parent.contents[self.parent.index(self)]

        #Find the two elements that would be next to each other if
        #this element (and any children) hadn't been parsed. Connect
        #the two.
        last_child = self._last_descendant()
        next_element = last_child.next_element

        if (self.previous_element is not None and
            self.previous_element != next_element):
            self.previous_element.next_element = next_element
        if next_element is not None and next_element != self.previous_element:
            next_element.previous_element = self.previous_element
        self.previous_element = None
        last_child.next_element = None

        self.parent = None
        if (self.previous_sibling is not None
            and self.previous_sibling != self.next_sibling):
            self.previous_sibling.next_sibling = self.next_sibling
        if (self.next_sibling is not None
            and self.next_sibling != self.previous_sibling):
            self.next_sibling.previous_sibling = self.previous_sibling
        self.previous_sibling = self.next_sibling = None
        return self

    def _last_descendant(self, is_initialized=True, accept_self=True):
        "Finds the last element beneath this object to be parsed."
        if is_initialized and self.next_sibling:
            last_child = self.next_sibling.previous_element
        else:
            last_child = self
            while isinstance(last_child, Tag) and last_child.contents:
                last_child = last_child.contents[-1]
        if not accept_self and last_child == self:
            last_child = None
        return last_child
    # BS3: Not part of the API!
    _lastRecursiveChild = _last_descendant

    def insert(self, position, new_child):
        if new_child is self:
            raise ValueError("Cannot insert a tag into itself.")
        if (isinstance(new_child, str)
            and not isinstance(new_child, NavigableString)):
            new_child = NavigableString(new_child)

        position = min(position, len(self.contents))
        if hasattr(new_child, 'parent') and new_child.parent is not None:
            # We're 'inserting' an element that's already one
            # of this object's children.
            if new_child.parent is self:
                current_index = self.index(new_child)
                if current_index < position:
                    # We're moving this element further down the list
                    # of this object's children. That means that when
                    # we extract this element, our target index will
                    # jump down one.
                    position -= 1
            new_child.extract()

        new_child.parent = self
        previous_child = None
        if position == 0:
            new_child.previous_sibling = None
            new_child.previous_element = self
        else:
            previous_child = self.contents[position - 1]
            new_child.previous_sibling = previous_child
            new_child.previous_sibling.next_sibling = new_child
            new_child.previous_element = previous_child._last_descendant(False)
        if new_child.previous_element is not None:
            new_child.previous_element.next_element = new_child

        new_childs_last_element = new_child._last_descendant(False)

        if position >= len(self.contents):
            new_child.next_sibling = None

            parent = self
            parents_next_sibling = None
            while parents_next_sibling is None and parent is not None:
                parents_next_sibling = parent.next_sibling
                parent = parent.parent
                if parents_next_sibling is not None:
                    # We found the element that comes next in the document.
                    break
            if parents_next_sibling is not None:
                new_childs_last_element.next_element = parents_next_sibling
            else:
                # The last element of this tag is the last element in
                # the document.
                new_childs_last_element.next_element = None
        else:
            next_child = self.contents[position]
            new_child.next_sibling = next_child
            if new_child.next_sibling is not None:
                new_child.next_sibling.previous_sibling = new_child
            new_childs_last_element.next_element = next_child

        if new_childs_last_element.next_element is not None:
            new_childs_last_element.next_element.previous_element = new_childs_last_element
        self.contents.insert(position, new_child)

    def append(self, tag):
        """Appends the given tag to the contents of this tag."""
        self.insert(len(self.contents), tag)

    def insert_before(self, predecessor):
        """Makes the given element the immediate predecessor of this one.

        The two elements will have the same parent, and the given element
        will be immediately before this one.
        """
        if self is predecessor:
            raise ValueError("Can't insert an element before itself.")
        parent = self.parent
        if parent is None:
            raise ValueError(
                "Element has no parent, so 'before' has no meaning.")
        # Extract first so that the index won't be screwed up if they
        # are siblings.
        if isinstance(predecessor, PageElement):
            predecessor.extract()
        index = parent.index(self)
        parent.insert(index, predecessor)

    def insert_after(self, successor):
        """Makes the given element the immediate successor of this one.

        The two elements will have the same parent, and the given element
        will be immediately after this one.
        """
        if self is successor:
            raise ValueError("Can't insert an element after itself.")
        parent = self.parent
        if parent is None:
            raise ValueError(
                "Element has no parent, so 'after' has no meaning.")
        # Extract first so that the index won't be screwed up if they
        # are siblings.
        if isinstance(successor, PageElement):
            successor.extract()
        index = parent.index(self)
        parent.insert(index+1, successor)

    def find_next(self, name=None, attrs=OrderedDict(), text=None, **kwargs):
        """Returns the first item that matches the given criteria and
        appears after this Tag in the document."""
        return self._find_one(self.find_all_next, name, attrs, text, **kwargs)
    findNext = find_next  # BS3

    def find_all_next(self, name=None, attrs=OrderedDict(), text=None, limit=None,
                    **kwargs):
        """Returns all items that match the given criteria and appear
        after this Tag in the document."""
        return self._find_all(name, attrs, text, limit, self.next_elements,
                             **kwargs)
    findAllNext = find_all_next  # BS3

    def find_next_sibling(self, name=None, attrs=OrderedDict(), text=None, **kwargs):
        """Returns the closest sibling to this Tag that matches the
        given criteria and appears after this Tag in the document."""
        return self._find_one(self.find_next_siblings, name, attrs, text,
                             **kwargs)
    findNextSibling = find_next_sibling  # BS3

    def find_next_siblings(self, name=None, attrs=OrderedDict(), text=None, limit=None,
                           **kwargs):
        """Returns the siblings of this Tag that match the given
        criteria and appear after this Tag in the document."""
        return self._find_all(name, attrs, text, limit,
                              self.next_siblings, **kwargs)
    findNextSiblings = find_next_siblings   # BS3
    fetchNextSiblings = find_next_siblings  # BS2

    def find_previous(self, name=None, attrs=OrderedDict(), text=None, **kwargs):
        """Returns the first item that matches the given criteria and
        appears before this Tag in the document."""
        return self._find_one(
            self.find_all_previous, name, attrs, text, **kwargs)
    findPrevious = find_previous  # BS3

    def find_all_previous(self, name=None, attrs=OrderedDict(), text=None, limit=None,
                        **kwargs):
        """Returns all items that match the given criteria and appear
        before this Tag in the document."""
        return self._find_all(name, attrs, text, limit, self.previous_elements,
                           **kwargs)
    findAllPrevious = find_all_previous  # BS3
    fetchPrevious = find_all_previous    # BS2

    def find_previous_sibling(self, name=None, attrs=OrderedDict(), text=None, **kwargs):
        """Returns the closest sibling to this Tag that matches the
        given criteria and appears before this Tag in the document."""
        return self._find_one(self.find_previous_siblings, name, attrs, text,
                             **kwargs)
    findPreviousSibling = find_previous_sibling  # BS3

    def find_previous_siblings(self, name=None, attrs=OrderedDict(), text=None,
                               limit=None, **kwargs):
        """Returns the siblings of this Tag that match the given
        criteria and appear before this Tag in the document."""
        return self._find_all(name, attrs, text, limit,
                              self.previous_siblings, **kwargs)
    findPreviousSiblings = find_previous_siblings   # BS3
    fetchPreviousSiblings = find_previous_siblings  # BS2

    def find_parent(self, name=None, attrs=OrderedDict(), **kwargs):
        """Returns the closest parent of this Tag that matches the given
        criteria."""
        # NOTE: We can't use _find_one because findParents takes a different
        # set of arguments.
        r = None
        l = self.find_parents(name, attrs, 1, **kwargs)
        if l:
            r = l[0]
        return r
    findParent = find_parent  # BS3

    def find_parents(self, name=None, attrs=OrderedDict(), limit=None, **kwargs):
        """Returns the parents of this Tag that match the given
        criteria."""

        return self._find_all(name, attrs, None, limit, self.parents,
                             **kwargs)
    findParents = find_parents   # BS3
    fetchParents = find_parents  # BS2

    @property
    def next(self):
        return self.next_element

    @property
    def previous(self):
        return self.previous_element

    #These methods do the real heavy lifting.

    def _find_one(self, method, name, attrs, text, **kwargs):
        r = None
        l = method(name, attrs, text, 1, **kwargs)
        if l:
            r = l[0]
        return r

    def _find_all(self, name, attrs, text, limit, generator, **kwargs):
        "Iterates over a generator looking for things that match."

        if text is None and 'string' in kwargs:
            text = kwargs['string']
            del kwargs['string']

        if isinstance(name, SoupStrainer):
            strainer = name
        else:
            strainer = SoupStrainer(name, attrs, text, **kwargs)

        if text is None and not limit and not attrs and not kwargs:
            if name is True or name is None:
                # Optimization to find all tags.
                result = (element for element in generator
                          if isinstance(element, Tag))
                return ResultSet(strainer, result)
            elif isinstance(name, str):
                # Optimization to find all tags with a given name.
                result = (element for element in generator
                          if isinstance(element, Tag)
                            and element.name == name)
                return ResultSet(strainer, result)
        results = ResultSet(strainer)
        while True:
            try:
                i = next(generator)
            except StopIteration:
                break
            if i:
                found = strainer.search(i)
                if found:
                    results.append(found)
                    if limit and len(results) >= limit:
                        break
        return results

    #These generators can be used to navigate starting from both
    #NavigableStrings and Tags.
    @property
    def next_elements(self):
        i = self.next_element
        while i is not None:
            yield i
            i = i.next_element

    @property
    def next_siblings(self):
        i = self.next_sibling
        while i is not None:
            yield i
            i = i.next_sibling

    @property
    def previous_elements(self):
        i = self.previous_element
        while i is not None:
            yield i
            i = i.previous_element

    @property
    def previous_siblings(self):
        i = self.previous_sibling
        while i is not None:
            yield i
            i = i.previous_sibling

    @property
    def parents(self):
        i = self.parent
        while i is not None:
            yield i
            i = i.parent

    # Methods for supporting CSS selectors.

    tag_name_re = re.compile('^[a-zA-Z0-9][-.a-zA-Z0-9:_]*$')

    # /^([a-zA-Z0-9][-.a-zA-Z0-9:_]*)\[(\w+)([=~\|\^\$\*]?)=?"?([^\]"]*)"?\]$/
    #   \---------------------------/  \---/\-------------/    \-------/
    #     |                              |         |               |
    #     |                              |         |           The value
    #     |                              |    ~,|,^,$,* or =
    #     |                           Attribute
    #    Tag
    attribselect_re = re.compile(
        r'^(?P<tag>[a-zA-Z0-9][-.a-zA-Z0-9:_]*)?\[(?P<attribute>[\w-]+)(?P<operator>[=~\|\^\$\*]?)' +
        r'=?"?(?P<value>[^\]"]*)"?\]$'
        )

    def _attr_value_as_string(self, value, default=None):
        """Force an attribute value into a string representation.

        A multi-valued attribute will be converted into a
        space-separated stirng.
        """
        value = self.get(value, default)
        if isinstance(value, list) or isinstance(value, tuple):
            value =" ".join(value)
        return value

    def _tag_name_matches_and(self, function, tag_name):
        if not tag_name:
            return function
        else:
            def _match(tag):
                return tag.name == tag_name and function(tag)
            return _match

    def _attribute_checker(self, operator, attribute, value=''):
        """Create a function that performs a CSS selector operation.

        Takes an operator, attribute and optional value. Returns a
        function that will return True for elements that match that
        combination.
        """
        if operator == '=':
            # string representation of `attribute` is equal to `value`
            return lambda el: el._attr_value_as_string(attribute) == value
        elif operator == '~':
            # space-separated list representation of `attribute`
            # contains `value`
            def _includes_value(element):
                attribute_value = element.get(attribute, [])
                if not isinstance(attribute_value, list):
                    attribute_value = attribute_value.split()
                return value in attribute_value
            return _includes_value
        elif operator == '^':
            # string representation of `attribute` starts with `value`
            return lambda el: el._attr_value_as_string(
                attribute, '').startswith(value)
        elif operator == '$':
            # string represenation of `attribute` ends with `value`
            return lambda el: el._attr_value_as_string(
                attribute, '').endswith(value)
        elif operator == '*':
            # string representation of `attribute` contains `value`
            return lambda el: value in el._attr_value_as_string(attribute, '')
        elif operator == '|':
            # string representation of `attribute` is either exactly
            # `value` or starts with `value` and then a dash.
            def _is_or_starts_with_dash(element):
                attribute_value = element._attr_value_as_string(attribute, '')
                return (attribute_value == value or attribute_value.startswith(
                        value + '-'))
            return _is_or_starts_with_dash
        else:
            return lambda el: el.has_attr(attribute)

    # Old non-property versions of the generators, for backwards
    # compatibility with BS3.
    def nextGenerator(self):
        return self.next_elements

    def nextSiblingGenerator(self):
        return self.next_siblings

    def previousGenerator(self):
        return self.previous_elements

    def previousSiblingGenerator(self):
        return self.previous_siblings

    def parentGenerator(self):
        return self.parents


class NavigableString(str, PageElement):

    PREFIX = ''
    SUFFIX = ''

    def __new__(cls, value):
        """Create a new NavigableString.

        When unpickling a NavigableString, this method is called with
        the string in DEFAULT_OUTPUT_ENCODING. That encoding needs to be
        passed in to the superclass's __new__ or the superclass won't know
        how to handle non-ASCII characters.
        """
        if isinstance(value, str):
            u = str.__new__(cls, value)
        else:
            u = str.__new__(cls, value, DEFAULT_OUTPUT_ENCODING)
        u.setup()
        return u

    def __copy__(self):
        """A copy of a NavigableString has the same contents and class
        as the original, but it is not connected to the parse tree.
        """
        return type(self)(self)

    def __getnewargs__(self):
        return (str(self),)

    def __getattr__(self, attr):
        """text.string gives you text. This is for backwards
        compatibility for Navigable*String, but for CData* it lets you
        get the string without the CData wrapper."""
        if attr == 'string':
            return self
        else:
            raise AttributeError(
                "'%s' object has no attribute '%s'" % (
                    self.__class__.__name__, attr))

    def output_ready(self, formatter="minimal"):
        output = self.format_string(self, formatter)
        return self.PREFIX + output + self.SUFFIX

    @property
    def name(self):
        return None

    @name.setter
    def name(self, name):
        raise AttributeError("A NavigableString cannot be given a name.")

class PreformattedString(NavigableString):
    """A NavigableString not subject to the normal formatting rules.

    The string will be passed into the formatter (to trigger side effects),
    but the return value will be ignored.
    """

    def output_ready(self, formatter="minimal"):
        """CData strings are passed into the formatter.
        But the return value is ignored."""
        self.format_string(self, formatter)
        return self.PREFIX + self + self.SUFFIX

class CData(PreformattedString):

    PREFIX = '<![CDATA['
    SUFFIX = ']]>'

class ProcessingInstruction(PreformattedString):

    PREFIX = '<?'
    SUFFIX = '>'

class Comment(PreformattedString):

    PREFIX = '<!--'
    SUFFIX = '-->'


class Declaration(PreformattedString):
    PREFIX = '<!'
    SUFFIX = '!>'


class Doctype(PreformattedString):

    @classmethod
    def for_name_and_ids(cls, name, pub_id, system_id):
        value = name or ''
        if pub_id is not None:
            value += ' PUBLIC "%s"' % pub_id
            if system_id is not None:
                value += '\n "%s"' % system_id
        elif system_id is not None:
            value += ' SYSTEM "%s"' % system_id

        return Doctype(value)

    PREFIX = '<!DOCTYPE '
    SUFFIX = '>\n'


class Tag(PageElement):

    """Represents a found HTML tag with its attributes and contents."""

    def __init__(self, parser=None, builder=None, name=None, namespace=None,
                 prefix=None, attrs=None, parent=None, previous=None):
        "Basic constructor."

        if parser is None:
            self.parser_class = None
        else:
            # We don't actually store the parser object: that lets extracted
            # chunks be garbage-collected.
            self.parser_class = parser.__class__
        if name is None:
            raise ValueError("No value provided for new tag's name.")
        self.name = name
        self.namespace = namespace
        self.prefix = prefix
        if attrs is None:
            attrs = OrderedDict()
        elif attrs:
            if builder is not None and builder.cdata_list_attributes:
                attrs = builder._replace_cdata_list_attribute_values(
                    self.name, attrs)
            else:
                attrs = OrderedDict(attrs)
        else:
            attrs = OrderedDict(attrs)
        self.attrs = attrs
        self.contents = []
        self.setup(parent, previous)
        self.hidden = False

        # Set up any substitutions, such as the charset in a META tag.
        if builder is not None:
            builder.set_up_substitutions(self)
            self.can_be_empty_element = builder.can_be_empty_element(name)
        else:
            self.can_be_empty_element = False

    parserClass = _alias("parser_class")  # BS3

    def __copy__(self):
        """A copy of a Tag is a new Tag, unconnected to the parse tree.
        Its contents are a copy of the old Tag's contents.
        """
        clone = type(self)(None, self.builder, self.name, self.namespace,
                           self.nsprefix, self.attrs)
        for attr in ('can_be_empty_element', 'hidden'):
            setattr(clone, attr, getattr(self, attr))
        for child in self.contents:
            clone.append(child.__copy__())
        return clone

    @property
    def is_empty_element(self):
        """Is this tag an empty-element tag? (aka a self-closing tag)

        A tag that has contents is never an empty-element tag.

        A tag that has no contents may or may not be an empty-element
        tag. It depends on the builder used to create the tag. If the
        builder has a designated list of empty-element tags, then only
        a tag whose name shows up in that list is considered an
        empty-element tag.

        If the builder has no designated list of empty-element tags,
        then any tag with no contents is an empty-element tag.
        """
        return len(self.contents) == 0 and self.can_be_empty_element
    isSelfClosing = is_empty_element  # BS3

    @property
    def is_non_breaking_inline_tag(self):
        # used only for pretty printing of html to prevent returns after tags
        # from introducing spaces where none are desired
        return self.name in NON_BREAKING_INLINE_TAGS and not self._is_xml

    @property
    def string(self):
        """Convenience property to get the single string within this tag.

        :Return: If this tag has a single string child, return value
         is that string. If this tag has no children, or more than one
         child, return value is None. If this tag has one child tag,
         return value is the 'string' attribute of the child tag,
         recursively.
        """
        if len(self.contents) != 1:
            return None
        child = self.contents[0]
        if isinstance(child, NavigableString):
            return child
        return child.string

    @string.setter
    def string(self, string):
        self.clear()
        self.append(string.__class__(string))

    def _all_strings(self, strip=False, types=(NavigableString, CData)):
        """Yield all strings of certain classes, possibly stripping them.

        By default, yields only NavigableString and CData objects. So
        no comments, processing instructions, etc.
        """
        for descendant in self.descendants:
            if (
                (types is None and not isinstance(descendant, NavigableString))
                or
                (types is not None and type(descendant) not in types)):
                continue
            if strip:
                descendant = descendant.strip()
                if len(descendant) == 0:
                    continue
            yield descendant

    strings = property(_all_strings)

    @property
    def stripped_strings(self):
        for string in self._all_strings(True):
            yield string

    def get_text(self, separator="", strip=False,
                 types=(NavigableString, CData)):
        """
        Get all child strings, concatenated using the given separator.
        """
        return separator.join([s for s in self._all_strings(
                    strip, types=types)])
    getText = get_text
    text = property(get_text)

    def decompose(self):
        """Recursively destroys the contents of this tree."""
        self.extract()
        i = self
        while i is not None:
            next = i.next_element
            i.__dict__.clear()
            i.contents = []
            i = next

    def clear(self, decompose=False):
        """
        Extract all children. If decompose is True, decompose instead.
        """
        if decompose:
            for element in self.contents[:]:
                if isinstance(element, Tag):
                    element.decompose()
                else:
                    element.extract()
        else:
            for element in self.contents[:]:
                element.extract()

    def index(self, element):
        """
        Find the index of a child by identity, not value. Avoids issues with
        tag.contents.index(element) getting the index of equal elements.
        """
        for i, child in enumerate(self.contents):
            if child is element:
                return i
        raise ValueError("Tag.index: element not in tag")

    def get(self, key, default=None):
        """Returns the value of the 'key' attribute for the tag, or
        the value given for 'default' if it doesn't have that
        attribute."""
        return self.attrs.get(key, default)

    def has_attr(self, key):
        return key in self.attrs

    def __hash__(self):
        return str(self).__hash__()

    def __getitem__(self, key):
        """tag[key] returns the value of the 'key' attribute for the tag,
        and throws an exception if it's not there."""
        return self.attrs[key]

    def __iter__(self):
        "Iterating over a tag iterates over its contents."
        return iter(self.contents)

    def __len__(self):
        "The length of a tag is the length of its list of contents."
        return len(self.contents)

    def __contains__(self, x):
        return x in self.contents

    def __bool__(self):
        "A tag is non-None even if it has no contents."
        return True

    def __nonzero__(self):
        "A tag is non-None even if it has no contents."
        return True

    def __setitem__(self, key, value):
        """Setting tag[key] sets the value of the 'key' attribute for the
        tag."""
        self.attrs[key] = value

    def __delitem__(self, key):
        "Deleting tag[key] deletes all 'key' attributes for the tag."
        self.attrs.pop(key, None)

    def __call__(self, *args, **kwargs):
        """Calling a tag like a function is the same as calling its
        find_all() method. Eg. tag('a') returns a list of all the A tags
        found within this tag."""
        return self.find_all(*args, **kwargs)

    def __getattr__(self, tag):
        #print "Getattr %s.%s" % (self.__class__, tag)
        if len(tag) > 3 and tag.endswith('Tag'):
            # BS3: soup.aTag -> "soup.find("a")
            tag_name = tag[:-3]
            warnings.warn(
                '.%sTag is deprecated, use .find("%s") instead.' % (
                    tag_name, tag_name))
            return self.find(tag_name)
        # We special case contents to avoid recursion.
        elif not tag.startswith("__") and not tag=="contents":
            return self.find(tag)
        raise AttributeError(
            "'%s' object has no attribute '%s'" % (self.__class__, tag))

    def __eq__(self, other):
        """Returns true iff this tag has the same name, the same attributes,
        and the same contents (recursively) as the given tag."""
        if self is other:
            return True
        if (not hasattr(other, 'name') or
            not hasattr(other, 'attrs') or
            not hasattr(other, 'contents') or
            self.name != other.name or
            self.attrs != other.attrs or
            len(self) != len(other)):
            return False
        for i, my_child in enumerate(self.contents):
            if my_child != other.contents[i]:
                return False
        return True

    def __ne__(self, other):
        """Returns true iff this tag is not identical to the other tag,
        as defined in __eq__."""
        return not self == other

    def __repr__(self, encoding="unicode-escape"):
        """Renders this tag as a string."""
        # "The return value must be a string object", i.e. Unicode
        return self.decode()

    def __str__(self):
        return self.decode()

    def encode(self, encoding=DEFAULT_OUTPUT_ENCODING,
               indent_level=None, formatter="minimal",
               errors="xmlcharrefreplace", indent_chars=" "):
        # Turn the data structure into Unicode, then encode the
        # Unicode.
        u = self.decode(indent_level, encoding, formatter, indent_chars)
        return u.encode(encoding, errors)

    def _should_pretty_print(self, indent_level):
        """Should this tag be pretty-printed?"""
        return (
            indent_level is not None and
            ((self.name not in HTMLAwareEntitySubstitution.preformatted_tags 
              and self.name not in NON_BREAKING_INLINE_TAGS)
             or self._is_xml))

    def decode(self, indent_level=None,
               eventual_encoding=DEFAULT_OUTPUT_ENCODING,
               formatter="minimal", indent_chars=" "):
        """Returns a Unicode representation of this tag and its contents.

        :param eventual_encoding: The tag is destined to be
           encoded into this encoding. This method is _not_
           responsible for performing that encoding. This information
           is passed in so that it can be substituted in if the
           document contains a <META> tag that mentions the document's
           encoding.
        """

        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        attrs = []
        if self.attrs:
            for key, val in sorted(self.attrs.items()):
                if val is None:
                    decoded = key
                else:
                    if isinstance(val, list) or isinstance(val, tuple):
                        val = ' '.join(val)
                    elif not isinstance(val, str):
                        val = str(val)
                    elif (
                        isinstance(val, AttributeValueWithCharsetSubstitution)
                        and eventual_encoding is not None):
                        val = val.encode(eventual_encoding)

                    text = self.format_string(val, formatter)
                    decoded = (
                        str(key) + '='
                        + EntitySubstitution.quoted_attribute_value(text))
                attrs.append(decoded)
        close = ''
        closeTag = ''

        prefix = ''
        if self.prefix:
            prefix = self.prefix + ":"

        if self.is_empty_element:
            close = '/'
        else:
            closeTag = '</%s%s>' % (prefix, self.name)

        pretty_print = self._should_pretty_print(indent_level)
        space = ''
        indent_space = ''
        if indent_level is not None:
            indent_space = (indent_chars * (indent_level - 1))
        if pretty_print:
            space = indent_space
            indent_contents = indent_level + 1
        else:
            indent_contents = None
        contents = self.decode_contents(
            indent_contents, eventual_encoding, formatter, indent_chars)

        if self.hidden:
            # This is the 'document root' object.
            s = contents
        else:
            s = []
            attribute_string = ''
            if attrs:
                attribute_string = ' ' + ' '.join(attrs)
            if indent_level is not None:
                # Even if this particular tag is not pretty-printed,
                # we should indent up to the start of the tag.
                s.append(indent_space)
            s.append('<%s%s%s%s>' % (
                    prefix, self.name, attribute_string, close))
            if pretty_print:
                s.append("\n")
            s.append(contents)
            if pretty_print and contents and contents[-1] != "\n":
                s.append("\n")
            if pretty_print and closeTag:
                s.append(space)
            s.append(closeTag)
            if indent_level is not None and closeTag and self.next_sibling:
                # Even if this particular tag is not pretty-printed,
                # we're now done with the tag, and we should add a
                # newline if appropriate.
                s.append("\n")
            s = ''.join(s)
        return s

    def prettify(self, encoding=None, formatter="minimal", indent_chars=" "):
        if encoding is None:
            return self.decode(True, formatter=formatter, indent_chars=indent_chars)
        else:
            return self.encode(encoding, True, formatter=formatter, indent_chars=indent_chars)

    def decode_contents(self, indent_level=None,
                       eventual_encoding=DEFAULT_OUTPUT_ENCODING,
                       formatter="minimal", indent_chars=" "):
        """Renders the contents of this tag as a Unicode string.

        :param indent_level: Each line of the rendering will be
           indented this many spaces.

        :param eventual_encoding: The tag is destined to be
           encoded into this encoding. This method is _not_
           responsible for performing that encoding. This information
           is passed in so that it can be substituted in if the
           document contains a <META> tag that mentions the document's
           encoding.

        :param formatter: The output formatter responsible for converting
           entities to Unicode characters.
        """
        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        pretty_print = (indent_level is not None)
        s = []
        for c in self:
            text = None
            if isinstance(c, NavigableString):
                text = c.output_ready(formatter)
            elif isinstance(c, Tag):
                s.append(c.decode(indent_level, eventual_encoding, formatter, indent_chars))
            if text and indent_level and not self.name == 'pre':
                text = text.strip()
            if text:
                if pretty_print and not self.name == 'pre':
                    s.append(indent_chars * (indent_level - 1))
                s.append(text)
                if pretty_print and not self.name == 'pre':
                    s.append("\n")
        return ''.join(s)

    def decodexml(self, indent_level=0, eventual_encoding=DEFAULT_OUTPUT_ENCODING, 
               formatter="minimal", indent_chars=" "):

        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        is_xmlparent = self.name.lower() in EBOOK_XML_PARENT_TAGS
        attrs = []
        if self.attrs:
            for key, val in sorted(self.attrs.items()):
                if val is None:
                    decoded = key
                else:
                    if isinstance(val, list) or isinstance(val, tuple):
                        val = ' '.join(val)
                    elif not isinstance(val, str):
                        val = str(val)
                    elif (
                        isinstance(val, AttributeValueWithCharsetSubstitution)
                        and eventual_encoding is not None):
                        val = val.encode(eventual_encoding)

                    text = self.format_string(val, formatter)
                    decoded = (
                        str(key) + '='
                        + EntitySubstitution.quoted_attribute_value(text))
                attrs.append(decoded)

        prefix = ''
        if self.prefix:
            prefix = self.prefix + ":"

        # for pure xml, a self closing tag with only whitespace 
        # "contents" should be treated as empty
        if self.can_be_empty_element:
            tagcontents = self.string
            if tagcontents is not None and len(tagcontents.strip()) == 0:
                self.contents = []
        
        close = ''
        closeTag = ''
        if self.is_empty_element:
            close = '/'
        else:
            closeTag = '</%s%s>' % (prefix, self.name)

        indent_space = (indent_chars * (indent_level - 1))
        indent_contents = indent_level
        if is_xmlparent or self.hidden:
            indent_contents = indent_level + 1

        contents = self.decodexml_contents(indent_contents, eventual_encoding, formatter, indent_chars)
        if self.hidden:
            # This is the 'document root' object.
            s = contents
        else:
            s = []
            attribute_string = ''
            if attrs:
                attribute_string = ' ' + ' '.join(attrs)
            s.append(indent_space)
            s.append('<%s%s%s%s>' % (prefix, self.name, attribute_string, close))
            if is_xmlparent:
                s.append("\n")
            s.append(contents)
            if contents and contents[-1] != "\n" and is_xmlparent or self.is_empty_element:
                s.append("\n")
            if closeTag and is_xmlparent:
                s.append(indent_space)
            s.append(closeTag)
            if closeTag and self.next_sibling:
                s.append("\n")
            s = ''.join(s)
        return s

    def decodexml_contents(self, indent_level=0, eventual_encoding=DEFAULT_OUTPUT_ENCODING, 
                        formatter="minimal", indent_chars=" "):
        """Renders the contents of this tag as a Unicode string.
        """
        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        is_xmlparent = self.name.lower() in EBOOK_XML_PARENT_TAGS
        s = []
        for c in self:
            text = None
            if isinstance(c, NavigableString):
                text = c.output_ready(formatter)
            elif isinstance(c, Tag):
                val = c.decodexml(indent_level, eventual_encoding, formatter, indent_chars)
                s.append(val)
            if text:
                text = text.strip()
            if text:
                if is_xmlparent and len(s) == 0:
                    s.append(indent_chars * (indent_level - 1))
                s.append(text)
        return ''.join(s)

    def serialize_xhtml(self, eventual_encoding=DEFAULT_OUTPUT_ENCODING, formatter="minimal"):
        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        prefix = ''
        close = ''
        closeTag = ''
        attrs = []
        if self.attrs:
            for key, val in sorted(self.attrs.items()):
                if val is None:
                    ntext = key
                else:
                    if isinstance(val, list) or isinstance(val, tuple):
                        val = ' '.join(val)
                    elif not isinstance(val, str):
                        val = str(val)
                    elif (isinstance(val, AttributeValueWithCharsetSubstitution) and 
                          eventual_encoding is not None):
                        val = val.encode(eventual_encoding)
                    text = self.format_string(val, formatter)
                    ntext = (str(key) + '=' + EntitySubstitution.quoted_attribute_value(text))
                attrs.append(ntext)

        contents = self.serialize_xhtml_contents(eventual_encoding, formatter)

        in_xml_ns = self.namespace != 'http://www.w3.org/1999/xhtml'
        testcontents = contents.strip()

        if self.prefix:
            prefix = self.prefix + ":"

        if self.name in VOID_TAGS or (in_xml_ns and testcontents==""):
            close = '/'
        else:
            closeTag = '</%s%s>' % (prefix, self.name)

        # strip extraneous whitespace before the primary closing tag
        if self.name in SPECIAL_HANDLING_TAGS:
            contents = contents.strip()
            contents += "\n"

        if self.hidden:
            # This is the 'document root' object.
            s = contents
        else:
            s = []
            attribute_string = ''
            if attrs:
                attribute_string = ' ' + ' '.join(attrs)
            s.append('<%s%s%s%s>' % (prefix, self.name, attribute_string, close))
            if self.name in SPECIAL_HANDLING_TAGS:
                s.append("\n")
            s.append(contents)
            s.append(closeTag)
            if self.name in SPECIAL_HANDLING_TAGS:
                s.append("\n")
            s = ''.join(s)
        return s

    def serialize_xhtml_contents(self, eventual_encoding=DEFAULT_OUTPUT_ENCODING, formatter="minimal"):

        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        s = []
        for c in self:
            text = None
            if isinstance(c, Comment):
                text = Comment(c).output_ready(formatter)
                s.append(text)
            elif isinstance(c, CData):
                text = CData(c).output_ready(formatter)
                s.append(text)
            elif isinstance(c, NavigableString):
                text = c.output_ready(formatter)
                s.append(text)
            elif isinstance(c, Tag):
                s.append(c.serialize_xhtml(eventual_encoding, formatter))
        return ''.join(s)

    def prettyprint_xhtml(self, indent_level=0, eventual_encoding=DEFAULT_OUTPUT_ENCODING, 
               formatter="minimal", indent_chars=" "):

        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        is_structural = self.name in STRUCTURAL_TAGS
        is_inline = self.name in NON_BREAKING_INLINE_TAGS

        # build attribute string
        attribs = []
        atts = ""
        if self.attrs:
            for key, val in sorted(self.attrs.items()):
                if val is None:
                    decoded = key
                else:
                    if isinstance(val, list) or isinstance(val, tuple):
                        val = ' '.join(val)
                    elif not isinstance(val, str):
                        val = str(val)
                    elif (
                        isinstance(val, AttributeValueWithCharsetSubstitution)
                        and eventual_encoding is not None):
                        val = val.encode(eventual_encoding)

                    text = self.format_string(val, formatter)
                    decoded = (
                        str(key) + '='
                        + EntitySubstitution.quoted_attribute_value(text))
                attribs.append(decoded)
            atts = " " + " ".join(attribs)


        # get tag content
        contents=""
        is_void_tag = self.name in VOID_TAGS
        if not is_void_tag:
            if is_structural:
                contents = self.prettyprint_xhtml_contents(indent_level+1, eventual_encoding, formatter, indent_chars)
            else:
                contents = self.prettyprint_xhtml_contents(indent_level, eventual_encoding, formatter, indent_chars)

        if self.hidden:
            # This is the 'document root' object.
            return contents

        in_xml_ns = self.namespace != 'http://www.w3.org/1999/xhtml'
        testcontents = contents.strip()
        single = self.name in VOID_TAGS or (in_xml_ns and testcontents == "")

        prefix = ''
        if self.prefix:
            prefix = self.prefix + ":"

        is_keepwhitespace = self.name in PRESERVE_WHITESPACE_TAGS
        if not is_keepwhitespace and not is_inline:
            contents = contents.rstrip()

        indent_space = (indent_chars * (indent_level - 1))

        # handle self-closed tags with no content first
        if single:
            selfclosetag = '<%s%s%s/>' % (prefix, self.name, atts)
            if is_inline:
                # always add newline after br tags when they are children of structural tags
                if (self.name == "br") and self.parent.name in STRUCTURAL_TAGS:
                    selfclosetag += "\n"
                return selfclosetag
            return indent_space + selfclosetag + "\n"

        # handle the general case
        starttag = '<%s%s%s>' % (prefix, self.name, atts)
        closetag = '</%s%s>' % (prefix, self.name)
        results = ""
        if is_structural:
            results = indent_space + starttag
            if contents != "":
                results += "\n" + contents + "\n" + indent_space
            results += closetag + "\n"
        elif is_inline:
            results = starttag
            results += contents
            results += closetag
        else:
            results = indent_space + starttag
            if not is_keepwhitespace:
                contents = contents.lstrip()
            results += contents
            results += closetag + "\n"
        return results

    def prettyprint_xhtml_contents(self, indent_level=0, eventual_encoding=DEFAULT_OUTPUT_ENCODING, 
                        formatter="minimal", indent_chars=" "):
        """Renders the contents of this tag as a Unicode string.
        """
        # First off, turn a string formatter into a function. This
        # will stop the lookup from happening over and over again.
        if not isinstance(formatter, Callable):
            formatter = self._formatter_for_name(formatter)

        is_structural = self.name in STRUCTURAL_TAGS
        is_inline = self.name in NON_BREAKING_INLINE_TAGS
        is_keepwhitespace = self.name in PRESERVE_WHITESPACE_TAGS
        indent_space = (indent_chars * (indent_level - 1))
        last_char = "x"
        contains_block_tags = False

        if is_structural or self.hidden:
            last_char = "\n"

        s = []

        for c in self:
            text = None
            if isinstance(c, Comment):
                text = Comment(c).output_ready(formatter)
                s.append(text)
            elif isinstance(c, CData):
                text = CData(c).output_ready(formatter)
                s.append(text)
            elif isinstance(c, NavigableString):
                text = c.output_ready(formatter)
                tval = text
                is_whitespace = (tval.strip() == "")

                # handle pure whitespace differently
                if is_whitespace:
                    if is_keepwhitespace:
                        s.append(text)
                    elif is_inline or self.name in OTHER_TEXTHOLDING_TAGS:
                        if last_char not in " \t\v\f\r\n":
                            s.append(" ")
                        else:
                            s.append("")
                    else:
                        # ignore this whitespace
                        s.append("")

                # handle all other text
                else:
                    if is_structural and last_char == "\n":
                        s.append(indent_space)
                        text = text.lstrip()
                    s.append(text)

            # handle tags
            elif isinstance(c, Tag):
                val = c.prettyprint_xhtml(indent_level, eventual_encoding, formatter, indent_chars)
                # track if contains block tags and append newline and prepend newline if needed
                if not c.name in NON_BREAKING_INLINE_TAGS:
                    contains_block_tags = True
                    if last_char != "\n":
                        s.append("\n")
                        last_char = "\n"
                # if child of a structual tag is inline and follows a newline, indent it properly
                if is_structural and c.name in NON_BREAKING_INLINE_TAGS and last_char == '\n':
                    s.append(indent_space)
                    val = val.lstrip()
                s.append(val)

            else:
                s.append("")

            # update last_char
            last_element = s[-1]
            if last_element != "":
                last_char = last_element[-1:]

        # after processing all children, handle inline tags that contain block level tags
        if is_inline and contains_block_tags:
            if last_char != "\n":
                s.append("\n")
            s.append(indent_space)

        return ''.join(s)

    def encode_contents(
        self, indent_level=None, encoding=DEFAULT_OUTPUT_ENCODING,
        formatter="minimal", indent_chars=" "):
        """Renders the contents of this tag as a bytestring.

        :param indent_level: Each line of the rendering will be
           indented this many spaces.

        :param eventual_encoding: The bytestring will be in this encoding.

        :param formatter: The output formatter responsible for converting
           entities to Unicode characters.
        """

        contents = self.decode_contents(indent_level, encoding, formatter, indent_chars)
        return contents.encode(encoding)

    # Old method for BS3 compatibility
    def renderContents(self, encoding=DEFAULT_OUTPUT_ENCODING,
                       prettyPrint=False, indentLevel=0):
        if not prettyPrint:
            indentLevel = None
        return self.encode_contents(
            indent_level=indentLevel, encoding=encoding)

    #Soup methods

    def find(self, name=None, attrs=OrderedDict(), recursive=True, text=None,
             **kwargs):
        """Return only the first child of this Tag matching the given
        criteria."""
        r = None
        l = self.find_all(name, attrs, recursive, text, 1, **kwargs)
        if l:
            r = l[0]
        return r
    findChild = find

    def find_all(self, name=None, attrs=OrderedDict(), recursive=True, text=None,
                 limit=None, **kwargs):
        """Extracts a list of Tag objects that match the given
        criteria.  You can specify the name of the Tag and any
        attributes you want the Tag to have.

        The value of a key-value pair in the 'attrs' map can be a
        string, a list of strings, a regular expression object, or a
        callable that takes a string and returns whether or not the
        string matches for some custom definition of 'matches'. The
        same is true of the tag name."""

        generator = self.descendants
        if not recursive:
            generator = self.children
        return self._find_all(name, attrs, text, limit, generator, **kwargs)
    findAll = find_all       # BS3
    findChildren = find_all  # BS2

    #Generator methods
    @property
    def children(self):
        # return iter() to make the purpose of the method clear
        return iter(self.contents)  # XXX This seems to be untested.

    @property
    def descendants(self):
        if not len(self.contents):
            return
        stopNode = self._last_descendant().next_element
        current = self.contents[0]
        while current is not stopNode:
            yield current
            current = current.next_element

    # CSS selector code

    _selector_combinators = ['>', '+', '~']
    _select_debug = False
    def select_one(self, selector):
        """Perform a CSS selection operation on the current element."""
        value = self.select(selector, limit=1)
        if value:
            return value[0]
        return None

    def select(self, selector, _candidate_generator=None, limit=None):
        """Perform a CSS selection operation on the current element."""

        # Remove whitespace directly after the grouping operator ','
        # then split into tokens.
        tokens = re.sub(',[\s]*',',', selector).split()
        current_context = [self]

        if tokens[-1] in self._selector_combinators:
            raise ValueError(
                'Final combinator "%s" is missing an argument.' % tokens[-1])

        if self._select_debug:
            print('Running CSS selector "%s"' % selector)

        for index, token_group in enumerate(tokens):
            new_context = []
            new_context_ids = set([])

            # Grouping selectors, ie: p,a
            grouped_tokens = token_group.split(',')
            if '' in grouped_tokens:
                raise ValueError('Invalid group selection syntax: %s' % token_group)

            if tokens[index-1] in self._selector_combinators:
                # This token was consumed by the previous combinator. Skip it.
                if self._select_debug:
                    print('  Token was consumed by the previous combinator.')
                continue

            for token in grouped_tokens:
                if self._select_debug:
                    print(' Considering token "%s"' % token)
                recursive_candidate_generator = None
                tag_name = None

                # Each operation corresponds to a checker function, a rule
                # for determining whether a candidate matches the
                # selector. Candidates are generated by the active
                # iterator.
                checker = None

                m = self.attribselect_re.match(token)
                if m is not None:
                    # Attribute selector
                    tag_name, attribute, operator, value = m.groups()
                    checker = self._attribute_checker(operator, attribute, value)

                elif '#' in token:
                    # ID selector
                    tag_name, tag_id = token.split('#', 1)
                    def id_matches(tag):
                        return tag.get('id', None) == tag_id
                    checker = id_matches

                elif '.' in token:
                    # Class selector
                    tag_name, klass = token.split('.', 1)
                    classes = set(klass.split('.'))
                    def classes_match(candidate):
                        return classes.issubset(candidate.get('class', []))
                    checker = classes_match

                elif ':' in token:
                    # Pseudo-class
                    tag_name, pseudo = token.split(':', 1)
                    if tag_name == '':
                        raise ValueError(
                            "A pseudo-class must be prefixed with a tag name.")
                    pseudo_attributes = re.match('([a-zA-Z\d-]+)\(([a-zA-Z\d]+)\)', pseudo)
                    found = []
                    if pseudo_attributes is None:
                        pseudo_type = pseudo
                        pseudo_value = None
                    else:
                        pseudo_type, pseudo_value = pseudo_attributes.groups()
                    if pseudo_type == 'nth-of-type':
                        try:
                            pseudo_value = int(pseudo_value)
                        except:
                            raise NotImplementedError(
                                'Only numeric values are currently supported for the nth-of-type pseudo-class.')
                        if pseudo_value < 1:
                            raise ValueError(
                                'nth-of-type pseudo-class value must be at least 1.')
                        class Counter(object):
                            def __init__(self, destination):
                                self.count = 0
                                self.destination = destination

                            def nth_child_of_type(self, tag):
                                self.count += 1
                                if self.count == self.destination:
                                    return True
                                if self.count > self.destination:
                                    # Stop the generator that's sending us
                                    # these things.
                                    raise StopIteration()
                                return False
                        checker = Counter(pseudo_value).nth_child_of_type
                    else:
                        raise NotImplementedError(
                            'Only the following pseudo-classes are implemented: nth-of-type.')

                elif token == '*':
                    # Star selector -- matches everything
                    pass
                elif token == '>':
                    # Run the next token as a CSS selector against the
                    # direct children of each tag in the current context.
                    recursive_candidate_generator = lambda tag: tag.children
                elif token == '~':
                    # Run the next token as a CSS selector against the
                    # siblings of each tag in the current context.
                    recursive_candidate_generator = lambda tag: tag.next_siblings
                elif token == '+':
                    # For each tag in the current context, run the next
                    # token as a CSS selector against the tag's next
                    # sibling that's a tag.
                    def next_tag_sibling(tag):
                        yield tag.find_next_sibling(True)
                    recursive_candidate_generator = next_tag_sibling

                elif self.tag_name_re.match(token):
                    # Just a tag name.
                    tag_name = token
                else:
                    raise ValueError(
                        'Unsupported or invalid CSS selector: "%s"' % token)
                if recursive_candidate_generator:
                    # This happens when the selector looks like  "> foo".
                    #
                    # The generator calls select() recursively on every
                    # member of the current context, passing in a different
                    # candidate generator and a different selector.
                    #
                    # In the case of "> foo", the candidate generator is
                    # one that yields a tag's direct children (">"), and
                    # the selector is "foo".
                    next_token = tokens[index+1]
                    def recursive_select(tag):
                        if self._select_debug:
                            print('    Calling select("%s") recursively on %s %s' % (next_token, tag.name, tag.attrs))
                            print('-' * 40)
                        for i in tag.select(next_token, recursive_candidate_generator):
                            if self._select_debug:
                                print('(Recursive select picked up candidate %s %s)' % (i.name, i.attrs))
                            yield i
                        if self._select_debug:
                            print('-' * 40)
                    _use_candidate_generator = recursive_select
                elif _candidate_generator is None:
                    # By default, a tag's candidates are all of its
                    # children. If tag_name is defined, only yield tags
                    # with that name.
                    if self._select_debug:
                        if tag_name:
                            check = "[any]"
                        else:
                            check = tag_name
                        print('   Default candidate generator, tag name="%s"' % check)
                    if self._select_debug:
                        # This is redundant with later code, but it stops
                        # a bunch of bogus tags from cluttering up the
                        # debug log.
                        def default_candidate_generator(tag):
                            for child in tag.descendants:
                                if not isinstance(child, Tag):
                                    continue
                                if tag_name and not child.name == tag_name:
                                    continue
                                yield child
                        _use_candidate_generator = default_candidate_generator
                    else:
                        _use_candidate_generator = lambda tag: tag.descendants
                else:
                    _use_candidate_generator = _candidate_generator

                count = 0
                for tag in current_context:
                    if self._select_debug:
                        print("    Running candidate generator on %s %s" % (
                            tag.name, repr(tag.attrs)))
                    for candidate in _use_candidate_generator(tag):
                        if not isinstance(candidate, Tag):
                            continue
                        if tag_name and candidate.name != tag_name:
                            continue
                        if checker is not None:
                            try:
                                result = checker(candidate)
                            except StopIteration:
                                # The checker has decided we should no longer
                                # run the generator.
                                break
                        if checker is None or result:
                            if self._select_debug:
                                print("     SUCCESS %s %s" % (candidate.name, repr(candidate.attrs)))
                            if id(candidate) not in new_context_ids:
                                # If a tag matches a selector more than once,
                                # don't include it in the context more than once.
                                new_context.append(candidate)
                                new_context_ids.add(id(candidate))
                                if limit and len(new_context) >= limit:
                                    break
                        elif self._select_debug:
                            print("     FAILURE %s %s" % (candidate.name, repr(candidate.attrs)))


            current_context = new_context

        if self._select_debug:
            print("Final verdict:")
            for i in current_context:
                print(" %s %s" % (i.name, i.attrs))
        return current_context

    # Old names for backwards compatibility
    def childGenerator(self):
        return self.children

    def recursiveChildGenerator(self):
        return self.descendants

    def has_key(self, key):
        """This was kind of misleading because has_key() (attributes)
        was different from __in__ (contents). has_key() is gone in
        Python 3, anyway."""
        warnings.warn('has_key is deprecated. Use has_attr("%s") instead.' % (
                key))
        return self.has_attr(key)

# Next, a couple classes to represent queries and their results.
class SoupStrainer(object):
    """Encapsulates a number of ways of matching a markup element (tag or
    text)."""

    def __init__(self, name=None, attrs=OrderedDict(), text=None, **kwargs):
        self.name = self._normalize_search_value(name)
        if not isinstance(attrs, dict):
            # Treat a non-dict value for attrs as a search for the 'class'
            # attribute.
            kwargs['class'] = attrs
            attrs = None

        if 'class_' in kwargs:
            # Treat class_="foo" as a search for the 'class'
            # attribute, overriding any non-dict value for attrs.
            kwargs['class'] = kwargs['class_']
            del kwargs['class_']

        if kwargs:
            if attrs:
                attrs = attrs.copy()
                attrs.update(kwargs)
            else:
                attrs = kwargs
        normalized_attrs = OrderedDict()
        for key, value in list(attrs.items()):
            normalized_attrs[key] = self._normalize_search_value(value)

        self.attrs = normalized_attrs
        self.text = self._normalize_search_value(text)

    def _normalize_search_value(self, value):
        # Leave it alone if it's a Unicode string, a callable, a
        # regular expression, a boolean, or None.
        if (isinstance(value, str) or isinstance(value, Callable) or hasattr(value, 'match')
            or isinstance(value, bool) or value is None):
            return value

        # If it's a bytestring, convert it to Unicode, treating it as UTF-8.
        if isinstance(value, bytes):
            return value.decode("utf8")

        # If it's listlike, convert it into a list of strings.
        if hasattr(value, '__iter__'):
            new_value = []
            for v in value:
                if (hasattr(v, '__iter__') and not isinstance(v, bytes)
                    and not isinstance(v, str)):
                    # This is almost certainly the user's mistake. In the
                    # interests of avoiding infinite loops, we'll let
                    # it through as-is rather than doing a recursive call.
                    new_value.append(v)
                else:
                    new_value.append(self._normalize_search_value(v))
            return new_value

        # Otherwise, convert it into a Unicode string.
        return str(value)

    def __str__(self):
        if self.text:
            return self.text
        else:
            return "%s|%s" % (self.name, self.attrs)

    def search_tag(self, markup_name=None, markup_attrs=OrderedDict()):
        found = None
        markup = None
        if isinstance(markup_name, Tag):
            markup = markup_name
            markup_attrs = markup
        call_function_with_tag_data = (
            isinstance(self.name, Callable)
            and not isinstance(markup_name, Tag))

        if ((not self.name)
            or call_function_with_tag_data
            or (markup and self._matches(markup, self.name))
            or (not markup and self._matches(markup_name, self.name))):
            if call_function_with_tag_data:
                match = self.name(markup_name, markup_attrs)
            else:
                match = True
                markup_attr_map = None
                for attr, match_against in list(self.attrs.items()):
                    if not markup_attr_map:
                        if hasattr(markup_attrs, 'get'):
                            markup_attr_map = markup_attrs
                        else:
                            markup_attr_map = OrderedDict()
                            for k, v in markup_attrs:
                                markup_attr_map[k] = v
                    attr_value = markup_attr_map.get(attr)
                    if not self._matches(attr_value, match_against):
                        match = False
                        break
            if match:
                if markup:
                    found = markup
                else:
                    found = markup_name
        if found and self.text and not self._matches(found.string, self.text):
            found = None
        return found
    searchTag = search_tag

    def search(self, markup):
        # print 'looking for %s in %s' % (self, markup)
        found = None
        # If given a list of items, scan it for a text element that
        # matches.
        if hasattr(markup, '__iter__') and not isinstance(markup, (Tag, str)):
            for element in markup:
                if isinstance(element, NavigableString) \
                       and self.search(element):
                    found = element
                    break
        # If it's a Tag, make sure its name or attributes match.
        # Don't bother with Tags if we're searching for text.
        elif isinstance(markup, Tag):
            if not self.text or self.name or self.attrs:
                found = self.search_tag(markup)
        # If it's text, make sure the text matches.
        elif isinstance(markup, NavigableString) or \
                 isinstance(markup, str):
            if not self.name and not self.attrs and self._matches(markup, self.text):
                found = markup
        else:
            raise Exception(
                "I don't know how to match against a %s" % markup.__class__)
        return found

    def _matches(self, markup, match_against):
        # print u"Matching %s against %s" % (markup, match_against)
        result = False
        if isinstance(markup, list) or isinstance(markup, tuple):
            # This should only happen when searching a multi-valued attribute
            # like 'class'.
            if (isinstance(match_against, str)
                and ' ' in match_against):
                # A bit of a special case. If they try to match "foo
                # bar" on a multivalue attribute's value, only accept
                # the literal value "foo bar"
                #
                # XXX This is going to be pretty slow because we keep
                # splitting match_against. But it shouldn't come up
                # too often.
                return (whitespace_re.split(match_against) == markup)
            else:
                for item in markup:
                    if self._matches(item, match_against):
                        return True
                return False

        if match_against is True:
            # True matches any non-None value.
            return markup is not None

        if isinstance(match_against, Callable):
            return match_against(markup)

        # Custom callables take the tag as an argument, but all
        # other ways of matching match the tag name as a string.
        if isinstance(markup, Tag):
            markup = markup.name

        # Ensure that `markup` is either a Unicode string, or None.
        markup = self._normalize_search_value(markup)

        if markup is None:
            # None matches None, False, an empty string, an empty list, and so on.
            return not match_against

        if isinstance(match_against, str):
            # Exact string match
            return markup == match_against

        if hasattr(match_against, 'match'):
            # Regexp match
            return match_against.search(markup)

        if hasattr(match_against, '__iter__'):
            # The markup must be an exact match against something
            # in the iterable.
            return markup in match_against


class ResultSet(list):
    """A ResultSet is just a list that keeps track of the SoupStrainer
    that created it."""
    def __init__(self, source, result=()):
        super(ResultSet, self).__init__(result)
        self.source = source
