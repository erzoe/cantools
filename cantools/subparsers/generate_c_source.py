from __future__ import print_function
import os
import re
import time

from .. import database


GENERATE_H_FMT = '''\
/**
 * The MIT License (MIT)
 *
 * Copyright (c) 2018 Erik Moqvist
 *
 * Permission is hereby granted, free of charge, to any person
 * obtaining a copy of this software and associated documentation
 * files (the "Software"), to deal in the Software without
 * restriction, including without limitation the rights to use, copy,
 * modify, merge, publish, distribute, sublicense, and/or sell copies
 * of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be
 * included in all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
 * EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 * MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 * NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
 * BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
 * ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 * CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

/**
 * This file was generated by cantools version {version} {date}.
 */

#ifndef {include_guard}
#define {include_guard}

#include <stdint.h>
#include <stdbool.h>
#include <unistd.h>

#ifndef EINVAL
#    define EINVAL -22
#endif

{frame_id_defines}

{signal_choice_val_defines}

{structs}
{declarations}
#endif
'''

GENERATE_C_FMT = '''\
/**
 * The MIT License (MIT)
 *
 * Copyright (c) 2018 Erik Moqvist
 *
 * Permission is hereby granted, free of charge, to any person
 * obtaining a copy of this software and associated documentation
 * files (the "Software"), to deal in the Software without
 * restriction, including without limitation the rights to use, copy,
 * modify, merge, publish, distribute, sublicense, and/or sell copies
 * of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be
 * included in all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
 * EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
 * MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 * NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
 * BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
 * ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
 * CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

/**
 * This file was generated by cantools version {version} {date}.
 */

#include <string.h>

#include "{header}"

#define ftoi(value) (*((uint32_t *)(&(value))))
#define itof(value) (*((float *)(&(value))))
#define dtoi(value) (*((uint64_t *)(&(value))))
#define itod(value) (*((double *)(&(value))))

{definitions}\
'''

STRUCT_FMT = '''\
/**
 * Signals in message {database_message_name}.
 *
{comments}
 */
struct {database_name}_{message_name}_t {{
{members}
}};
'''

DECLARATION_FMT = '''\
/**
 * Encode message {database_message_name}.
 *
 * @param[out] dst_p Buffer to encode the message into.
 * @param[in] src_p Data to encode.
 * @param[in] size Size of dst_p.
 *
 * @return Size of encoded data, or negative error code.
 */
ssize_t {database_name}_{message_name}_encode(
    uint8_t *dst_p,
    struct {database_name}_{message_name}_t *src_p,
    size_t size);

/**
 * Decode message {database_message_name}.
 *
 * @param[out] dst_p Object to decode the message into.
 * @param[in] src_p Message to decode.
 * @param[in] size Size of src_p.
 *
 * @return zero(0) or negative error code.
 */
int {database_name}_{message_name}_decode(
    struct {database_name}_{message_name}_t *dst_p,
    uint8_t *src_p,
    size_t size);
'''

IS_IN_RANGE_DECLARATION_FMT = '''\
/**
 * Check that given signal is in allowed range.
 *
 * @param[in] value Signal to check.
 *
 * @return true if in range, false otherwise.
 */
bool {database_name}_{message_name}_{signal_name}_is_in_range({type_name} value);
'''

DEFINITION_FMT = '''\
ssize_t {database_name}_{message_name}_encode(
    uint8_t *dst_p,
    struct {database_name}_{message_name}_t *src_p,
    size_t size)
{{
{encode_variables}\
    if (size < {message_length}) {{
        return (-EINVAL);
    }}

    memset(&dst_p[0], 0, {message_length});
{encode_body}
    return ({message_length});
}}

int {database_name}_{message_name}_decode(
    struct {database_name}_{message_name}_t *dst_p,
    uint8_t *src_p,
    size_t size)
{{
{decode_variables}\
    if (size < {message_length}) {{
        return (-EINVAL);
    }}

    memset(dst_p, 0, sizeof(*dst_p));
{decode_body}
    return (0);
}}
'''

IS_IN_RANGE_DEFINITION_FMT = '''\
bool {database_name}_{message_name}_{signal_name}_is_in_range({type_name} value)
{{
    return ({check});
}}
'''

EMPTY_DEFINITION_FMT = '''\
ssize_t {database_name}_{message_name}_encode(
    uint8_t *dst_p,
    struct {database_name}_{message_name}_t *src_p,
    size_t size)
{{
    return (0);
}}

int {database_name}_{message_name}_decode(
    struct {database_name}_{message_name}_t *dst_p,
    uint8_t *src_p,
    size_t size)
{{
    memset(dst_p, 0, sizeof(*dst_p));

    return (0);
}}
'''

SIGN_EXTENSION_FMT = '''
    if (dst_p->{name} & (1 << {shift})) {{
        dst_p->{name} |= {mask};
    }}

'''

SIGNAL_PARAM_COMMENT_FMT = '''\
 * @param {name} Value as on the CAN bus.
{comment}\
 *            Range: {range}
 *            Scale: {scale}
 *            Offset: {offset}\
'''


def _camel_to_snake_case(value):
    value = re.sub('( +)', r'_', value)
    value = re.sub('(:)', r'_', value)
    value = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', value)
    value = re.sub('(_+)', r'_', value)
    value = re.sub('([a-z0-9])([A-Z])', r'\1_\2', value).lower()
    value = re.sub('\.', '', value)

    return value


def _type_name(signal):
    type_name = None

    if signal.is_float:
        if signal.length == 32:
            type_name = 'float'
        elif signal.length == 64:
            type_name = 'double'
        else:
            print('warning: Floating point signal not 32 or 64 bits.')
    else:
        if signal.length <= 8:
            type_name = 'int8_t'
        elif signal.length <= 16:
            type_name = 'int16_t'
        elif signal.length <= 32:
            type_name = 'int32_t'
        elif signal.length <= 64:
            type_name = 'int64_t'
        else:
            print('warning: Signal lengths over 64 bits are not yet supported.')

        if type_name is not None:
            if not signal.is_signed:
                type_name = 'u' + type_name

    return type_name


def _get_type_suffix(type_name):
    try:
        return {
            'uint8_t': 'u',
            'uint16_t': 'u',
            'uint32_t': 'u',
            'int64_t': 'll',
            'uint64_t': 'ull',
            'float': 'f'
        }[type_name]
    except KeyError:
        return ''


def _get(value, default):
    if value is None:
        value = default

    return value


def _is_minimum_type_value(type_name, value):
    if type_name == 'int8_t':
        return value == -128
    elif type_name == 'int16_t':
        return value == -32768
    elif type_name == 'int32_t':
        return value == -2147483648
    elif type_name == 'int64_t':
        return value == -9223372036854775808
    elif type_name[0] == 'u':
        return value == 0
    else:
        return False


def _is_maximum_type_value(type_name, value):
    try:
        return {
            'int8_t': 127,
            'int16_t': 32767,
            'int32_t': 2147483647,
            'int64_t': 9223372036854775807,
            'uint8_t': 255,
            'uint16_t': 65535,
            'uint32_t': 4294967295,
            'uint64_t': 18446744073709551615
        }[type_name] == value
    except KeyError:
        return False


def _format_comment(comment):
    if comment:
        return '\n'.join([
            ' *            ' + line.rstrip()
            for line in comment.splitlines()
        ]) + '\n'
    else:
        return ''


def _format_decimal(value):
    if int(value) == value:
        value = int(value)

    return str(value)


def _format_range(signal):
    minimum = signal.decimal.minimum
    maximum = signal.decimal.maximum
    scale = signal.decimal.scale
    offset = signal.decimal.offset
    unit = _get(signal.unit, '-')

    if minimum is not None and maximum is not None:
        return '{}..{} ({}..{} {})'.format(
            _format_decimal((minimum - offset) / scale),
            _format_decimal((maximum - offset) / scale),
            minimum,
            maximum,
            unit)
    elif minimum is not None:
        return '{}.. ({}.. {})'.format(
            _format_decimal((minimum - offset) / scale),
            minimum,
            unit)
    elif maximum is not None:
        return '..{} (..{} {}'.format(
            _format_decimal((maximum - offset) / scale),
            maximum,
            unit)
    else:
        return '-'


def _generate_signal(signal):
    if signal.is_multiplexer or signal.multiplexer_ids:
        print('warning: Multiplexed signals are not yet supported.')

        return None, None

    type_name = _type_name(signal)

    if type_name is None:
        return None, None

    name = _camel_to_snake_case(signal.name)
    comment = _format_comment(signal.comment)
    range_ = _format_range(signal)
    scale = _get(signal.scale, '-')
    offset = _get(signal.offset, '-')

    comment = SIGNAL_PARAM_COMMENT_FMT.format(name=name,
                                              comment=comment,
                                              range=range_,
                                              scale=scale,
                                              offset=offset)
    member = '    {} {};'.format(type_name, name)

    choices = []
    if signal.choices:
        for value, text in sorted(signal.choices.items()):
            if not signal.is_signed:
                choice_fmt_str = '{choice_name}_{choice_text}_CHOICE ({choice_value}U)'
            else:
                choice_fmt_str = '{choice_name}_{choice_text}_CHOICE ({choice_value})'

            choices.append(choice_fmt_str.format(
                choice_name=name.upper(),
                choice_text=_camel_to_snake_case(text).upper(),
                choice_value=value))

    return comment, member, choices


def _signal_segments(signal, invert_shift):
    index, pos = divmod(signal.start, 8)
    left = signal.length

    while left > 0:
        if signal.byte_order == 'big_endian':
            if left > (pos + 1):
                length = (pos + 1)
                pos = 7
                shift = -(left - length)
                mask = ((1 << length) - 1)
            else:
                length = left
                mask = ((1 << length) - 1)

                if (pos - length) >= 0:
                    shift = (pos - length + 1)
                else:
                    shift = (8 - left)

                mask <<= (pos - length + 1)
        else:
            if left >= (8 - pos):
                length = (8 - pos)
                shift = (left - signal.length) + pos
                mask = ((1 << length) - 1)
                mask <<= pos
                pos = 0
            else:
                length = left
                mask = ((1 << length) - 1)
                shift = pos
                mask <<= pos

        if invert_shift:
            if shift < 0:
                shift = '<< {}'.format(-shift)
            else:
                shift = '>> {}'.format(shift)
        else:
            if shift < 0:
                shift = '>> {}'.format(-shift)
            else:
                shift = '<< {}'.format(shift)

        yield index, shift, mask

        left -= length
        index += 1


def _format_encode_code(message):
    body_per_index = {}
    variables = []
    conversions = []

    for signal in message.signals:
        signal_name = _camel_to_snake_case(signal.name)

        if signal.is_float:
            if signal.length == 32:
                variable = '    uint32_t {};'.format(signal_name)
                line = '    {0} = ftoi(src_p->{0});'.format(signal_name)
            else:
                variable = '    uint64_t {};'.format(signal_name)
                line = '    {0} = dtoi(src_p->{0});'.format(signal_name)

            variables.append(variable)
            conversions.append(line)

        for index, shift, mask in _signal_segments(signal, False):
            if index not in body_per_index:
                body_per_index[index] = []

            if signal.is_float:
                fmt = '    dst_p[{}] |= (({} {}) & 0x{:02x});'
            else:
                fmt = '    dst_p[{}] |= ((src_p->{} {}) & 0x{:02x});'

            line = fmt.format(index, signal_name, shift, mask)
            body_per_index[index].append(line)

    body = []

    for index in sorted(body_per_index):
        body += body_per_index[index]

    if variables:
        variables += ['', '']

    if conversions:
        conversions += ['']

    variables = '\n'.join(variables)

    body = conversions + body

    if body:
        body = [''] + body + ['']

    body = '\n'.join(body)

    return variables, body


def _format_decode_code(message):
    variables = []
    body = []
    conversions = []

    for signal in message.signals:
        signal_name = _camel_to_snake_case(signal.name)

        if signal.length <= 8:
            type_length = 8
        elif signal.length <= 16:
            type_length = 16
        elif signal.length <= 32:
            type_length = 32
        elif signal.length <= 64:
            type_length = 64

        for index, shift, mask in _signal_segments(signal, True):
            if signal.is_float:
                fmt = '    {} |= ((uint{}_t)(src_p[{}] & 0x{:02x}) {});'
            else:
                fmt = '    dst_p->{} |= ((uint{}_t)(src_p[{}] & 0x{:02x}) {});'

            line = fmt.format(signal_name, type_length, index, mask, shift)
            body.append(line)

        if signal.is_float:
            if signal.length == 32:
                variable = '    uint32_t {} = 0;'.format(signal_name)
                line = '    dst_p->{0} = itof({0});'.format(signal_name)
            else:
                variable = '    uint64_t {} = 0;'.format(signal_name)
                line = '    dst_p->{0} = itod({0});'.format(signal_name)

            variables.append(variable)
            conversions.append(line)
        elif signal.is_signed:
            mask = ((1 << (type_length - signal.length)) - 1)
            mask <<= signal.length
            formatted = SIGN_EXTENSION_FMT.format(name=signal_name,
                                                  shift=signal.length - 1,
                                                  mask=hex(mask))
            body.extend(formatted.splitlines())

    if variables:
        variables += ['', '']

    variables = '\n'.join(variables)

    if conversions:
        conversions = [''] + conversions + ['']

    body += conversions

    if body:
        if body[-1] == '':
            body = body[:-1]

        body = [''] + body + ['']

    body = '\n'.join(body)

    return variables, body


def _generate_struct(message):
    comments = []
    members = []
    choices = []

    for signal in message.signals:
        comment, member, signal_choice = _generate_signal(signal)

        if comment is not None:
            comments.append(comment)

        if member is not None:
            members.append(member)

        if signal_choice:
            signal_choice = ['{message_name}_{choice_str}'.format(
                message_name=_camel_to_snake_case(message.name).upper(),
                choice_str=choice) for choice in signal_choice]
            choices.append(signal_choice)

    if not comments:
        comments = [' * @param dummy Dummy signal in empty message.']

    if not members:
        members = ['    uint8_t dummy;']

    return comments, members, choices


def _generate_is_in_range(message):
    """Generate range checks for all signals in given message.

    """

    signals = []

    for signal in message.signals:
        scale = signal.decimal.scale
        offset = (signal.decimal.offset / scale)
        minimum = signal.decimal.minimum
        maximum = signal.decimal.maximum

        if minimum is not None:
            minimum = int(minimum / scale - offset)

        if maximum is not None:
            maximum = int(maximum / scale - offset)

        type_name = _type_name(signal)
        suffix = _get_type_suffix(type_name)
        checks = []

        if minimum is not None:
            if not _is_minimum_type_value(type_name, minimum):
                checks.append('(value >= {}{})'.format(minimum, suffix))

        if maximum is not None:
            if not _is_maximum_type_value(type_name, maximum):
                checks.append('(value <= {}{})'.format(maximum, suffix))

        if not checks:
            checks = ['true']
        elif len(checks) == 1:
            checks = [checks[0][1:-1]]

        checks = ' && '.join(checks)

        signals.append((_camel_to_snake_case(signal.name),
                        type_name,
                        checks))

    return signals


def _generate_message(database_name, message):
    message_name = _camel_to_snake_case(message.name)
    comments, members, choices = _generate_struct(message)
    is_in_range_declarations = []
    is_in_range_definitions = []

    for signal_name, type_name, check in _generate_is_in_range(message):
        is_in_range_declaration = IS_IN_RANGE_DECLARATION_FMT.format(
            database_name=database_name,
            message_name=message_name,
            signal_name=signal_name,
            type_name=type_name)
        is_in_range_declarations.append(is_in_range_declaration)
        is_in_range_definition = IS_IN_RANGE_DEFINITION_FMT.format(
            database_name=database_name,
            message_name=message_name,
            signal_name=signal_name,
            type_name=type_name,
            check=check)
        is_in_range_definitions.append(is_in_range_definition)

    struct_ = STRUCT_FMT.format(database_message_name=message.name,
                                message_name=message_name,
                                database_name=database_name,
                                comments='\n'.join(comments),
                                members='\n'.join(members))
    declaration = DECLARATION_FMT.format(database_name=database_name,
                                         database_message_name=message.name,
                                         message_name=message_name)
    declaration += '\n' + '\n'.join(is_in_range_declarations)

    if message.length > 0:
        encode_variables, encode_body = _format_encode_code(message)
        decode_variables, decode_body = _format_decode_code(message)
        definition = DEFINITION_FMT.format(database_name=database_name,
                                           database_message_name=message.name,
                                           message_name=message_name,
                                           message_length=message.length,
                                           encode_variables=encode_variables,
                                           encode_body=encode_body,
                                           decode_variables=decode_variables,
                                           decode_body=decode_body)
    else:
        definition = EMPTY_DEFINITION_FMT.format(database_name=database_name,
                                                 message_name=message_name)

    definition += '\n' + '\n'.join(is_in_range_definitions)

    frame_id_define = '#define {}_FRAME_ID_{} (0x{:02x}U)'.format(
        database_name.upper(),
        message_name.upper(),
        message.frame_id)

    choices = [
        [
            '#define {database_name}_{choice_str}'.format(
                database_name=database_name.upper(),
                choice_str=choice
            ) for choice in signal_choice
        ]
        for signal_choice in choices
    ]

    return struct_, declaration, definition, frame_id_define, choices


def _do_generate_c_source(args, version):
    dbase = database.load_file(args.infile,
                               encoding=args.encoding,
                               strict=not args.no_strict)

    basename = os.path.basename(args.infile)
    filename = os.path.splitext(basename)[0]
    filename_h = filename + '.h'
    filename_c = filename + '.c'
    date = time.ctime()
    include_guard = '__{}_H__'.format(filename.upper())
    structs = []
    declarations = []
    definitions = []
    frame_id_defines = []
    choices_defines = []

    for message in dbase.messages:
        (struct_,
         declaration,
         definition,
         frame_id_define,
         choices) = _generate_message(filename, message)

        structs.append(struct_)
        declarations.append(declaration)
        definitions.append(definition)
        frame_id_defines.append(frame_id_define)
        if choices:
            choices_defines.extend(choices)

    structs = '\n'.join(structs)
    declarations = '\n'.join(declarations)
    definitions = '\n'.join(definitions)
    frame_id_defines = '\n'.join(frame_id_defines)
    choices_defines = '\n\n'.join(['\n'.join(signal_choice) for signal_choice in choices_defines])

    with open(filename_h, 'w') as fout:
        fout.write(GENERATE_H_FMT.format(version=version,
                                         date=date,
                                         include_guard=include_guard,
                                         structs=structs,
                                         declarations=declarations,
                                         frame_id_defines=frame_id_defines,
                                         signal_choice_val_defines=choices_defines))

    with open(filename_c, 'w') as fout:
        fout.write(GENERATE_C_FMT.format(version=version,
                                         date=date,
                                         header=filename_h,
                                         definitions=definitions))

    print('Successfully generated {} and {}.'.format(filename_h, filename_c))


def add_subparser(subparsers):
    generate_c_source_parser = subparsers.add_parser(
        'generate_c_source',
        description='Generate C source code from given database file.')
    generate_c_source_parser.add_argument(
        '-e', '--encoding',
        default='utf-8',
        help='File encoding (default: utf-8).')
    generate_c_source_parser.add_argument(
        '--no-strict',
        action='store_true',
        help='Skip database consistency checks.')
    generate_c_source_parser.add_argument(
        'infile',
        help='Input database file.')
    generate_c_source_parser.set_defaults(func=_do_generate_c_source)
