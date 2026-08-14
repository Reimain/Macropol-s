{{ fullname | escape | underline }}

.. rubric:: Contents

{% if classes %}
.. autosummary::
   :nosignatures:
{% for item in classes %}
   ~{{ fullname }}.{{ item }}
{%- endfor %}
{% endif %}

{% if exceptions %}
.. autosummary::
   :nosignatures:
{% for item in exceptions %}
   ~{{ fullname }}.{{ item }}
{%- endfor %}
{% endif %}

{% if functions %}
.. autosummary::
   :nosignatures:
{% for item in functions %}
   ~{{ fullname }}.{{ item }}
{%- endfor %}
{% endif %}

.. automodule:: {{ fullname }}
   :members:
   :undoc-members:
   :show-inheritance:
   :member-order: bysource
   :special-members: __init__, __post_init__

{% block modules %}
{% if modules %}
.. rubric:: Submodules

.. autosummary::
   :toctree:
   :template: autosummary/module.rst
   :recursive:
{% for item in modules %}
   {{ item }}
{%- endfor %}
{% endif %}
{% endblock %}
